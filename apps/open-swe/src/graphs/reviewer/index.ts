import { END, START, StateGraph } from "@langchain/langgraph";
import {
  ReviewerGraphState,
  ReviewerGraphStateObj,
} from "@open-swe/shared/open-swe/reviewer/types";
import {
  GraphConfig,
  GraphConfiguration,
} from "@open-swe/shared/open-swe/types";
import {
  finalReview,
  generateReviewActions,
  initializeState,
  takeReviewerActions,
} from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";
import { diagnoseError } from "../shared/diagnose-error.js";

function takeReviewActionsOrFinalReview(
  state: ReviewerGraphState,
  config: GraphConfig,
): "take-review-actions" | "final-review" {
  const { reviewerMessages } = state;
  const lastMessage = reviewerMessages[reviewerMessages.length - 1];

  const maxReviewActions = config.configurable?.maxReviewActions ?? 30;
  const maxActionsCount = maxReviewActions * 2 + 1;
  if (
    isAIMessage(lastMessage) &&
    lastMessage.tool_calls?.length &&
    reviewerMessages.length < maxActionsCount
  ) {
    return "take-review-actions";
  }

  // If the last message does not have tool calls, continue to generate the final review.
  return "final-review";
}

const workflow = new StateGraph(ReviewerGraphStateObj, GraphConfiguration)
  .addNode("initialize-state", initializeState)
  .addNode("generate-review-actions", generateReviewActions)
  .addNode("take-review-actions", takeReviewerActions, {
    ends: ["generate-review-actions", "diagnose-reviewer-error"],
  })
  .addNode("diagnose-reviewer-error", diagnoseError)
  .addNode("final-review", finalReview)
  .addEdge(START, "initialize-state")
  .addEdge("initialize-state", "generate-review-actions")
  .addConditionalEdges(
    "generate-review-actions",
    takeReviewActionsOrFinalReview,
    ["take-review-actions", "final-review"],
  )
  .addEdge("take-review-actions", "generate-review-actions")
  .addEdge("diagnose-reviewer-error", "generate-review-actions")
  .addEdge("final-review", END);

export const graph = workflow.compile();
graph.name = "Open SWE - Reviewer";
