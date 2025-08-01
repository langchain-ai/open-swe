import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  GraphConfig,
  GraphState,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import { Command } from "@langchain/langgraph";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import {
  completePlanItem,
  getActivePlanItems,
  getActiveTask,
} from "@open-swe/shared/open-swe/tasks";
import {
  getCurrentPlanItem,
  getRemainingPlanItems,
} from "../../../utils/current-task.js";
import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { addTaskPlanToIssue } from "../../../utils/github/issue-task.js";
import { createMarkTaskCompletedToolFields } from "@open-swe/shared/open-swe/tools";
import {
  calculateConversationHistoryTokenCount,
  getMessagesSinceLastSummary,
  MAX_INTERNAL_TOKENS,
} from "../../../utils/tokens.js";
import { z } from "zod";

const logger = createLogger(LogLevel.INFO, "HandleCompletedTask");

export async function handleCompletedTask(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const markCompletedTool = createMarkTaskCompletedToolFields();
  const markCompletedMessage =
    state.internalMessages[state.internalMessages.length - 1];
  if (
    !isAIMessage(markCompletedMessage) ||
    !markCompletedMessage.tool_calls?.length ||
    !markCompletedMessage.tool_calls.some(
      (tc) => tc.name === markCompletedTool.name,
    )
  ) {
    throw new Error("Failed to find a tool call when checking task status.");
  }
  const toolCall = markCompletedMessage.tool_calls?.[0];
  if (!toolCall) {
    throw new Error(
      "Failed to generate a tool call when checking task status.",
    );
  }

  const activePlanItems = getActivePlanItems(state.taskPlan);
  const currentTask = getCurrentPlanItem(activePlanItems);
  const toolMessage = new ToolMessage({
    id: uuidv4(),
    tool_call_id: toolCall.id ?? "",
    content: `Saved task status as completed for task ${currentTask?.plan || "unknown"}`,
    name: toolCall.name,
  });

  const newMessages = [toolMessage];

  const newMessageList = [...state.internalMessages, ...newMessages];
  const wouldBeConversationHistoryToSummarize =
    await getMessagesSinceLastSummary(newMessageList, {
      excludeHiddenMessages: true,
      excludeCountFromEnd: 20,
    });
  const totalInternalTokenCount = calculateConversationHistoryTokenCount(
    wouldBeConversationHistoryToSummarize,
    {
      // Retain the last 20 messages from state
      excludeHiddenMessages: true,
      excludeCountFromEnd: 20,
    },
  );

  const summary = (toolCall.args as z.infer<typeof markCompletedTool.schema>)
    .completed_task_summary;

  // LLM marked as completed, so we need to update the plan to reflect that.
  const updatedPlanTasks = completePlanItem(
    state.taskPlan,
    getActiveTask(state.taskPlan).id,
    currentTask.index,
    summary,
  );
  // Update the github issue to reflect this task as completed.
  if (!isLocalMode(config)) {
    await addTaskPlanToIssue(
      {
        githubIssueId: state.githubIssueId,
        targetRepository: state.targetRepository,
      },
      config,
      updatedPlanTasks,
    );
  } else {
    logger.info("Skipping GitHub issue update in local mode");
  }

  const commandUpdate: GraphUpdate = {
    messages: newMessages,
    internalMessages: newMessages,
    // Even though there are no remaining tasks, still mark as completed so the UI reflects that the task is completed.
    taskPlan: updatedPlanTasks,
  };

  // This should in theory never happen, but ensure we route properly if it does.
  const remainingTask = getRemainingPlanItems(activePlanItems)?.[0];
  if (!remainingTask) {
    logger.info(
      "Found no remaining tasks in the plan during the check plan step. Continuing to the conclusion generation step.",
    );

    return new Command({
      goto: "route-to-review-or-conclusion",
      update: commandUpdate,
    });
  }

  if (totalInternalTokenCount >= MAX_INTERNAL_TOKENS) {
    logger.info(
      "Internal messages list is at or above the max token limit. Routing to summarize history step.",
      {
        totalInternalTokenCount,
        maxInternalTokenCount: MAX_INTERNAL_TOKENS,
      },
    );

    return new Command({
      goto: "summarize-history",
      update: commandUpdate,
    });
  }

  return new Command({
    goto: "generate-action",
    update: commandUpdate,
  });
}
