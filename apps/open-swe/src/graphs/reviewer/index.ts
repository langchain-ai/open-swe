import { END, START, StateGraph } from "@langchain/langgraph";
import {
  ReviewerGraphState,
  ReviewerGraphStateObj,
} from "@open-swe/shared/open-swe/reviewer/types";
import {
  GraphConfig,
  GraphConfiguration,
} from "@open-swe/shared/open-swe/types";
import { generateReviewActions, takeReviewerActions } from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";

function takeReviewActionsOrEnd(
  state: ReviewerGraphState,
  config: GraphConfig,
): "take-review-actions" | typeof END {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];
  // If the last message is a tool call, and we have executed less than 30 actions, take action.
  // Max actions count is calculated as: maxReviewActions * 2 + 1
  // This is because each action generates 2 messages (AI request + tool result) plus 1 initial human message
  const maxReviewActions = config.configurable?.maxReviewActions ?? 30;
  const maxActionsCount = maxReviewActions * 2 + 1;
  if (
    isAIMessage(lastMessage) &&
    lastMessage.tool_calls?.length &&
    messages.length < maxActionsCount
  ) {
    return "take-review-actions";
  }

  // If the last message does not have tool calls, continue to generate plan without modifications.
  return END;
}

// Add nodes for:
// generating final review
// initializing state (get head branch name, changed files, updated codebase tree)
// conditional edge for routing back to programmer
const workflow = new StateGraph(ReviewerGraphStateObj, GraphConfiguration)
  .addNode("generate-review-actions", generateReviewActions)
  .addNode("take-review-actions", takeReviewerActions)
  .addEdge(START, "generate-review-actions")
  .addConditionalEdges("generate-review-actions", takeReviewActionsOrEnd, [
    "take-review-actions",
    END,
  ])
  .addEdge("take-review-actions", "generate-review-actions");

export const graph = workflow.compile();
graph.name = "Open SWE - Reviewer";
