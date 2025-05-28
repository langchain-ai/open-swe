import { v4 as uuidv4 } from "uuid";
import { GraphConfig, GraphState, PlanItem } from "../types.js";
import { loadModel, Task } from "../utils/load-model.js";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { formatPlanPrompt } from "../utils/plan-prompt.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import {
  getMessageContentString,
  getMessageString,
} from "../utils/message/content.js";
import { removeLastTaskMessages } from "../utils/message/modify-array.js";
import { Command } from "@langchain/langgraph";
import { ConfigurableModel } from "langchain/chat_models/universal";
import { traceable } from "langsmith/traceable";

const taskSummarySysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

Your current task is to look at the conversation history, and generate a concise summary of the steps which were taken to complete the task.

Here are all of your tasks you've completed, remaining, and the current task you're working on. The completed tasks will include summaries of the steps taken to complete them:
{PLAN_PROMPT}

You MUST adhere to the following criteria when summarizing the conversation history:
  - Include insights, and learnings you've discovered about the codebase or specific files while completing the task.
    - You should NOT document scripts, file structure, or other context which could be categorized as 'general codebase context'. General codebase context (e.g. scripts, file structure, package managers, etc.) will be generated in a separate step. Inspect the codebase context string provided below for this information.
  - If files were created or modified, include short summaries of the changes made.
    - What file(s) were modified/created.
    - What content was added/removed.
    - If you had to make a change which required you to undo previous changes, include that information.
    - Do not include the actual changes you made, but rather high level bullet points containing context and descriptions on the modifications made.
  - Do not retain any full code snippets.
  - Do not retain any full file contents.
  - Ensure you have an understanding of the context and summaries you've already generated (provided by the user below) and do not repeat any information you've already included.
  - Do not duplicate ANY information. Ensure you carefully read and understand the task summaries generated above, and do not repeat any information you've already included.
  - You do not need to include specific codebase context here, as codebase context will be generated in a separate step. Your sole task is to generate a concise summary of this specific task you just completed.
  - Ensure your summary is as concise as possible, but useful for future context.

Here is the current state of the codebase context you've accumulated. Remember YOU SHOULD NOT INCLUDE ANY GENERAL CODEBASE CONTEXT IN YOUR TASK SUMMARY.
{CODEBASE_CONTEXT}

Ensure you do NOT include codebase context in your task summary, as we want to avoid including duplicate information.

With all of this in mind, please carefully summarize and condense the conversation history of the task you just completed, provided by the user below. Remember that this summary should ONLY include details about the completed task, and should NOT include any general codebase context.
Respond ONLY with the task summary. Do not include any additional information, or text before or after the task summary.
`;

const userContextMessage = `Here is the task you just completed:
{COMPLETED_TASK}

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
  - Any other relevant codebase information which may be useful for future context.
  - There should be NO task specific context here. ONLY include context about the codebase. This context should be generally applicable and not tied to the specifics of the task.

You have the following codebase context:
{CODEBASE_CONTEXT}

Please inspect this context, and given the rules above, please respond with a full, complete codebase context I can use for future context.
When responding, ensure:
  - You do not duplicate information.
  - You remove old/stale context from the existing codebase context string if recent messages contradict it.
  - You do NOT remove any information from the existing codebase context string if recent messages do not contradict it. We want to ensure we always have a complete picture of the codebase.
  - You modify/combine information from the existing codebase context string if if new information is provided which warrants a change.

Please be concise, clear and helpful. Omit any extraneous information. Respond ONLY with the codebase context. Do not include any additional information, or text before or after the codebase context.
`;

const updateCodebaseContextUserMessage = `Here is the task you just completed:
{COMPLETED_TASK}

The first message in the conversation history is the user's request. Messages from previously completed tasks have already been removed, in favor of task summaries.
With this in mind, please use the following conversation history to update the codebase context to include new relevant information.

Conversation history:
{CONVERSATION_HISTORY}`;

const logger = createLogger(LogLevel.INFO, "SummarizeTaskSteps");

const formatPrompt = (plan: PlanItem[], codebaseContext: string): string =>
  taskSummarySysPrompt
    .replace(
      "{PLAN_PROMPT}",
      formatPlanPrompt(plan, {
        useLastCompletedTask: true,
        includeSummaries: true,
      }),
    )
    .replace(
      "{CODEBASE_CONTEXT}",
      `<codebase-context>\n${codebaseContext || "No codebase context generated yet."}\n</codebase-context>`,
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

  return userContextMessage
    .replace("{COMPLETED_TASK}", completedTask.plan)
    .replace(
      "{CONVERSATION_HISTORY}",
      messages.map(getMessageString).join("\n"),
    );
};

const formatCodebaseContextPrompt = (codebaseContext: string): string =>
  updateCodebaseContextSysPrompt.replace(
    "{CODEBASE_CONTEXT}",
    `<codebase-context>\n${codebaseContext || "No codebase context generated yet."}\n</codebase-context>`,
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

async function generateTaskSummaryFunc(
  state: GraphState,
  model: ConfigurableModel,
): Promise<PlanItem[]> {
  const lastCompletedTask = state.plan.findLast((p) => p.completed);
  if (!lastCompletedTask) {
    throw new Error("Unable to find last completed task.");
  }

  logger.info(`Summarizing task steps...`);
  const response = await model.withConfig({ tags: ["nostream"] }).invoke([
    {
      role: "system",
      content: formatPrompt(state.plan, state.codebaseContext),
    },
    {
      role: "user",
      content: formatUserMessage(state.messages, state.plan),
    },
  ]);

  const contentString = getMessageContentString(response.content);
  const newPlanWithSummary = state.plan.map((p) => {
    if (p.index !== lastCompletedTask.index) {
      return p;
    }
    return {
      ...p,
      summary: contentString,
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
  logger.info(`Updating codebase context...`);
  const response = await model.withConfig({ tags: ["nostream"] }).invoke([
    {
      role: "system",
      content: formatCodebaseContextPrompt(state.codebaseContext),
    },
    {
      role: "user",
      content: formatUserCodebaseContextMessage(state.messages, state.plan),
    },
  ]);
  const contentString = getMessageContentString(response.content);
  return contentString;
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
