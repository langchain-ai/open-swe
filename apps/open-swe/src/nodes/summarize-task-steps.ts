import { v4 as uuidv4 } from "uuid";
import { GraphConfig, GraphState, GraphUpdate, PlanItem } from "../types.js";
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
import {
  completePlanItem,
  getActivePlanItems,
  getActiveTask,
} from "../utils/task-plan.js";
import { getCompletedPlanItems } from "../utils/current-task.js";

const taskSummarySysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

Your current task is to look at the conversation history, and generate a concise summary of the steps which were taken to complete the task.

Here are all of your tasks you've completed, remaining, and the current task you're working on. The completed tasks will include summaries of the steps taken to complete them:
{PLAN_PROMPT}

You MUST adhere to the following criteria when summarizing the conversation history:
  - Include insights, and learnings you've discovered about the codebase or specific files while completing the task.
    - You should NOT document scripts, file structure, or other context which could be categorized as 'general codebase context'. General codebase context is automatically included via the \`tree\` command.
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

const logger = createLogger(LogLevel.INFO, "SummarizeTaskSteps");

const formatPrompt = (plan: PlanItem[]): string =>
  taskSummarySysPrompt.replace(
    "{PLAN_PROMPT}",
    formatPlanPrompt(plan, {
      useLastCompletedTask: true,
      includeSummaries: true,
    }),
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

async function generateTaskSummaryFunc(
  state: GraphState,
  model: ConfigurableModel,
): Promise<{ planItemIndex: number; summary: string }> {
  const activePlanItems = getActivePlanItems(state.plan);
  const lastCompletedTask = getCompletedPlanItems(activePlanItems).pop();
  if (!lastCompletedTask) {
    throw new Error("Unable to find last completed task.");
  }

  logger.info(`Summarizing task steps...`);
  const response = await model.withConfig({ tags: ["nostream"] }).invoke([
    {
      role: "system",
      content: formatPrompt(activePlanItems),
    },
    {
      role: "user",
      content: formatUserMessage(state.messages, activePlanItems),
    },
  ]);

  return {
    planItemIndex: lastCompletedTask.index,
    summary: getMessageContentString(response.content),
  };
}

const generateTaskSummary = traceable(generateTaskSummaryFunc, {
  name: "generate-task-summary",
});

export async function summarizeTaskSteps(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const activePlanItems = getActivePlanItems(state.plan);
  const lastCompletedTask = getCompletedPlanItems(activePlanItems).pop();
  if (!lastCompletedTask) {
    throw new Error("Unable to find last completed task.");
  }

  const model = await loadModel(config, Task.SUMMARIZER);
  const taskSummary = await generateTaskSummary(state, model);
  const updatedTaskPlan = completePlanItem(
    state.plan,
    getActiveTask(state.plan).id,
    taskSummary.planItemIndex,
    taskSummary.summary,
  );

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

  const allTasksCompleted = activePlanItems.every((p) => p.completed);
  if (allTasksCompleted) {
    const commandUpdate: GraphUpdate = {
      messages: newMessagesStateUpdate,
      plan: updatedTaskPlan,
    };
    return new Command({
      goto: "generate-conclusion",
      update: commandUpdate,
    });
  }

  const commandUpdate: GraphUpdate = {
    messages: newMessagesStateUpdate,
    plan: updatedTaskPlan,
  };
  return new Command({
    goto: "generate-action",
    update: commandUpdate,
  });
}
