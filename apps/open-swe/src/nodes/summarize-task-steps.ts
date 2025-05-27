import { v4 as uuidv4 } from "uuid";
import { z } from "zod";
import { GraphConfig, GraphState, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import {
  formatPlanPrompt,
  formatPlanPromptWithSummaries,
} from "../utils/plan-prompt.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { getMessageString } from "../utils/message/content.js";
import { removeLastTaskMessages } from "../utils/message/modify-array.js";
import { Command } from "@langchain/langgraph";
import { ConfigurableModel } from "langchain/chat_models/universal";
import { traceable } from "langsmith/traceable";

const taskSummarySysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

Your current task is to look at the conversation history, and generate a concise summary of the steps which were taken to complete the task.

Here are all of your tasks you've completed, remaining, and the current task you're working on:

{PLAN_PROMPT}

You MUST adhere to the following criteria when summarizing the conversation history:
  - Consider including a section titled 'Key repository insights and learnings' which may include information, insights and learnings you've discovered about the codebase or specific files while completing the task.
    - This section should be concise, but still including enough information so following steps will not repeat any mistakes or go down rabbit holes which you already know about.
  - If changes were made to the repository during this task, ensure you include a section titled 'Repository modifications summary' which should include a short description of each change. Include information such as:
    - What file(s) were modified/created.
    - What content was added/removed.
    - If you had to make a change which required you to undo previous changes, include that information.
    - Do not include the actual changes you made, but rather high level bullet points containing context and descriptions on the modifications made.
  - Do not retain any full code snippets.
  - Do not retain any full file contents.
  - Ensure your summary is concise, but useful for future context.
  - Ensure you have an understanding of the context and summaries you've already generated (provided by the user below) and do not repeat any information you've already included.

With all of this in mind, please carefully summarize and condense the conversation history of the task you just completed, provided by the user below. Ensure you pass this condensed task summary to the \`condense_task_context\` tool.
`;

const userContextMessage = `Here is the task you just completed:
{COMPLETED_TASK}

And here is a list of the tasks you've already completed and generated task summaries for:
{TASKS_AND_SUMMARIES}

The first message in the conversation history is the user's request. Messages from previously completed tasks have already been removed, in favor of task summaries.
With this in mind, please use the following conversation history to generate a concise summary of the task you just completed.

Conversation history:
{CONVERSATION_HISTORY}`;

const updateCodebaseContextSysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

Your current task is to update the codebase context, given the recent actions taken by the agent.

The codebase context should contain:
 - Up to date information on the codebase file paths, and their contents.
  - Do not include entire file contents, but rather high level descriptions of what a file contains, and what it does.
 - Information on the software installed, and used in the codebase, including information such as version numbers, and dependencies.
 - High level context about the codebase structure, and style.
 - Any other relevant information which may be useful for future context.
  - Do not include task specific context here, this is extracted in a different step.

You have the following codebase context:
{CODEBASE_CONTEXT}

Please inspect this context, and given the rules above, please respond with a full, complete codebase context I can use for future context.
When responding, ensure:
 - You do not duplicate information.
 - You remove old/stale context from the existing codebase context string if recent messages contradict it.
 - You do NOT remove any information from the existing codebase context string if recent messages do not contradict it. We want to ensure we always have a complete picture of the codebase.
 - You modify/combine information from the existing codebase context string if if new information is provided which warrants a change.

Please be concise, clear and helpful. Omit any extraneous information. Call the \`update_codebase_context\` tool when you are finished.
`;

const updateCodebaseContextUserMessage = `Here is the task you just completed:
{COMPLETED_TASK}

The first message in the conversation history is the user's request. Messages from previously completed tasks have already been removed, in favor of task summaries.
With this in mind, please use the following conversation history to update the codebase context to include new relevant information.

Conversation history:
{CONVERSATION_HISTORY}`;

const updateCodebaseContextToolSchema = z.object({
  context: z
    .string()
    .describe(
      "The full, updated codebase context to be used for future context. Should include all of the existing codebase context, as well as any new information provided by the recent messages.",
    ),
});
const updateCodebaseContextTool = {
  name: "update_codebase_context",
  description: "Update the codebase context with the most recent changes.",
  schema: updateCodebaseContextToolSchema,
};

const logger = createLogger(LogLevel.INFO, "SummarizeTaskSteps");

const formatPrompt = (plan: PlanItem[]): string =>
  taskSummarySysPrompt.replace(
    "{PLAN_PROMPT}",
    formatPlanPrompt(plan, { useLastCompletedTask: true }),
  );

const formatUserMessage = (
  messages: BaseMessage[],
  plans: PlanItem[],
): string => {
  const completedTask = plans.find((p) => p.completed);
  if (!completedTask) {
    throw new Error(
      "No completed task found when trying to format user message for task summary.",
    );
  }
  const completedTasks = plans.filter(
    (p) => p.completed && p.index !== completedTask.index,
  );

  return userContextMessage
    .replace("{COMPLETED_TASK}", completedTask.plan)
    .replace(
      "{TASKS_AND_SUMMARIES}",
      formatPlanPromptWithSummaries(completedTasks),
    )
    .replace(
      "{CONVERSATION_HISTORY}",
      messages.map(getMessageString).join("\n"),
    );
};

const formatCodebaseContextPrompt = (codebaseContext: string): string =>
  updateCodebaseContextSysPrompt.replace(
    "{CODEBASE_CONTEXT}",
    codebaseContext ?? "No codebase context generated yet.",
  );

const formatUserCodebaseContextMessage = (
  messages: BaseMessage[],
  plans: PlanItem[],
): string => {
  const completedTask = plans.find((p) => p.completed);
  if (!completedTask) {
    throw new Error(
      "No completed task found when trying to format user message for task summary.",
    );
  }

  return updateCodebaseContextUserMessage
    .replace("{COMPLETED_TASK}", completedTask.plan)
    .replace(
      "{CONVERSATION_HISTORY}",
      messages.map(getMessageString).join("\n"),
    );
};

const condenseContextToolSchema = z.object({
  context: z
    .string()
    .describe(
      "The condensed context from the conversation history relevant to the recently completed task.",
    ),
});
const condenseContextTool = {
  name: "condense_task_context",
  description:
    "Condense the conversation history into a concise summary, while still retaining the most relevant and important snippets.",
  schema: condenseContextToolSchema,
};

async function generateTaskSummaryFunc(
  state: GraphState,
  model: ConfigurableModel,
): Promise<PlanItem[]> {
  const lastCompletedTask = state.plan.findLast((p) => p.completed);
  if (!lastCompletedTask) {
    throw new Error("Unable to find last completed task.");
  }

  const modelWithTools = model.bindTools([condenseContextTool], {
    tool_choice: condenseContextTool.name,
  });

  logger.info(`Summarizing task steps...`);
  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(state.plan),
    },
    {
      role: "user",
      content: formatUserMessage(state.messages, state.plan),
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("Failed to generate plan");
  }

  const contextSummary = (
    toolCall.args as z.infer<typeof condenseContextToolSchema>
  ).context;

  const newPlanWithSummary = state.plan.map((p) => {
    if (p.index !== lastCompletedTask.index) {
      return p;
    }
    return {
      ...p,
      summary: contextSummary,
    };
  });

  return newPlanWithSummary;
}

const generateTaskSummary = traceable(generateTaskSummaryFunc, {
  name: "generate_task_summary",
});

async function updateCodebaseContextFunc(
  state: GraphState,
  model: ConfigurableModel,
): Promise<string> {
  const modelWithTools = model.bindTools([updateCodebaseContextTool], {
    tool_choice: updateCodebaseContextTool.name,
  });

  logger.info(`Updating codebase context...`);
  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatCodebaseContextPrompt(state.codebaseContext),
    },
    {
      role: "user",
      content: formatUserCodebaseContextMessage(state.messages, state.plan),
    },
  ]);

  const toolCall = response.tool_calls?.[0];
  if (!toolCall) {
    throw new Error("Failed to update codebase context");
  }

  return (toolCall.args as z.infer<typeof updateCodebaseContextToolSchema>)
    .context;
}

const updateCodebaseContext = traceable(updateCodebaseContextFunc, {
  name: "update_codebase_context",
});

export async function summarizeTaskSteps(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const lastCompletedTask = state.plan.findLast((p) => p.completed);
  if (!lastCompletedTask) {
    throw new Error("Unable to find last completed task.");
  }

  const model = await loadModel(config, Task.SUMMARIZER);
  const [updatedPlan, updatedCodebaseContext] = await Promise.all([
    generateTaskSummary(state, model),
    updateCodebaseContext(state, model),
  ]);

  const removedMessages = removeLastTaskMessages(state.messages);
  logger.info(`Removing ${removedMessages.length} message(s) from state.`);

  const condensedTaskMessage = new AIMessage({
    id: uuidv4(),
    content: `Successfully condensed task context for task: "${lastCompletedTask.plan}". This task's summary can be found in the system prompt.`,
    additional_kwargs: {
      summary_message: true,
    },
  });
  const newMessagesStateUpdate = [...removedMessages, condensedTaskMessage];

  const allTasksCompleted = state.plan.every((p) => p.completed);
  if (allTasksCompleted) {
    return new Command({
      goto: "generate-conclusion",
      update: {
        messages: newMessagesStateUpdate,
        plan: updatedPlan,
        codebaseContext: updatedCodebaseContext,
      },
    });
  }

  return new Command({
    goto: "generate-action",
    update: {
      messages: newMessagesStateUpdate,
      plan: updatedPlan,
      codebaseContext: updatedCodebaseContext,
    },
  });
}
