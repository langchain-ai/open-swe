import { Command, END, Send, START, StateGraph } from "@langchain/langgraph";
import {
  GraphAnnotation,
  GraphConfig,
  GraphConfiguration,
  GraphState,
} from "@open-swe/shared/open-swe/types";
import {
  generateAction,
  takeAction,
  generateConclusion,
  openPullRequest,
  diagnoseError,
  requestHelp,
  updatePlan,
  summarizeHistory,
  handleCompletedTask,
} from "./nodes/index.js";
import { BaseMessage, isAIMessage } from "@langchain/core/messages";
import { initializeSandbox } from "../shared/initialize-sandbox.js";
import { graph as reviewerGraph } from "../reviewer/index.js";
import { getRemainingPlanItems } from "../../utils/current-task.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { createMarkTaskCompletedToolFields } from "@open-swe/shared/open-swe/tools";

function lastMessagesMissingToolCalls(
  messages: BaseMessage[],
  threshold: number,
) {
  const lastMessages = messages.slice(-threshold);
  if (!lastMessages.every(isAIMessage)) {
    // If some of the last messages are not AI messages, we should return false.
    return false;
  }
  return lastMessages.every((m) => !m.tool_calls?.length);
}

/**
 * Routes to the next appropriate node after taking action.
 * If the last message is an AI message with tool calls, it routes to "take-action".
 * Otherwise, it ends the process.
 *
 * @param {GraphState} state - The current graph state.
 * @returns {"route-to-review-or-conclusion" | "take-action" | "request-help" | "generate-action" | "handle-completed-task" | Send} The next node to execute, or END if the process should stop.
 */
function routeGeneratedAction(
  state: GraphState,
):
  | "route-to-review-or-conclusion"
  | "take-action"
  | "request-help"
  | "generate-action"
  | "handle-completed-task"
  | Send {
  const { internalMessages } = state;
  const lastMessage = internalMessages[internalMessages.length - 1];

  // If the message is an AI message, and it has tool calls, we should take action.
  if (isAIMessage(lastMessage) && lastMessage.tool_calls?.length) {
    const toolCall = lastMessage.tool_calls[0];
    if (toolCall.name === "request_human_help") {
      return "request-help";
    }

    if (
      toolCall.name === "update_plan" &&
      "update_plan_reasoning" in toolCall.args &&
      typeof toolCall.args?.update_plan_reasoning === "string"
    ) {
      // Need to return a `Send` here so that we can update the state to include the plan change request.
      return new Send("update-plan", {
        ...state,
        planChangeRequest: toolCall.args?.update_plan_reasoning,
      });
    }

    const taskMarkedCompleted =
      toolCall.name === createMarkTaskCompletedToolFields().name;
    if (taskMarkedCompleted) {
      return "handle-completed-task";
    }

    return "take-action";
  }

  const activePlanItems = getActivePlanItems(state.taskPlan);
  const hasRemainingTasks = getRemainingPlanItems(activePlanItems).length > 0;
  // If the model did not generate a tool call, but there are remaining tasks, we should route back to the generate action step.
  // Also add a check ensuring that the last to messages generated have tool calls. Otherwise we can end.
  if (hasRemainingTasks && !lastMessagesMissingToolCalls(internalMessages, 2)) {
    return "generate-action";
  }

  // No tool calls, route to reviewer subgraph
  return "route-to-review-or-conclusion";
}

/**
 * Conditional edge called after the reviewer. If there are no more actions to take, then open a PR.
 * Otherwise, route to generate actions to continue with the new tasks.
 */
function routeGenerateActionsOrEnd(
  state: GraphState,
): "generate-conclusion" | "generate-action" {
  const activePlanItems = getActivePlanItems(state.taskPlan);
  const allCompleted = activePlanItems.every((p) => p.completed);
  if (allCompleted) {
    return "generate-conclusion";
  }

  return "generate-action";
}

function routeToReviewOrConclusion(
  state: GraphState,
  config: GraphConfig,
): Command {
  const maxAllowedReviews = config.configurable?.maxReviewCount ?? 3;
  if (state.reviewsCount >= maxAllowedReviews) {
    return new Command({
      goto: "generate-conclusion",
    });
  }

  return new Command({
    goto: "reviewer-subgraph",
  });
}

const workflow = new StateGraph(GraphAnnotation, GraphConfiguration)
  .addNode("initialize", initializeSandbox)
  .addNode("generate-action", generateAction)
  .addNode("take-action", takeAction, {
    ends: ["generate-action", "diagnose-error"],
  })
  .addNode("update-plan", updatePlan)
  .addNode("handle-completed-task", handleCompletedTask, {
    ends: [
      "summarize-history",
      "generate-action",
      "route-to-review-or-conclusion",
    ],
  })
  .addNode("generate-conclusion", generateConclusion, {
    ends: ["open-pr", END],
  })
  .addNode("request-help", requestHelp, {
    ends: ["generate-action", END],
  })
  .addNode("route-to-review-or-conclusion", routeToReviewOrConclusion, {
    ends: ["generate-conclusion", "reviewer-subgraph"],
  })
  .addNode("reviewer-subgraph", reviewerGraph)
  .addNode("open-pr", openPullRequest)
  .addNode("diagnose-error", diagnoseError)
  .addNode("summarize-history", summarizeHistory)
  .addEdge(START, "initialize")
  .addEdge("initialize", "generate-action")
  .addConditionalEdges("generate-action", routeGeneratedAction, [
    "take-action",
    "request-help",
    "route-to-review-or-conclusion",
    "update-plan",
    "generate-action",
    "handle-completed-task",
  ])
  .addEdge("update-plan", "generate-action")
  .addEdge("diagnose-error", "generate-action")
  .addConditionalEdges("reviewer-subgraph", routeGenerateActionsOrEnd, [
    "generate-conclusion",
    "generate-action",
  ])
  .addEdge("summarize-history", "generate-action")
  .addEdge("open-pr", END);

// Zod types are messed up
export const graph = workflow.compile() as any;
graph.name = "Open SWE - Programmer";
