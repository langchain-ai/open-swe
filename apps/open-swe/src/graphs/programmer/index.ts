import { END, Send, START, StateGraph } from "@langchain/langgraph";
import {
  GraphAnnotation,
  GraphConfiguration,
  GraphState,
} from "@open-swe/shared/open-swe/types";
import {
  generateAction,
  takeAction,
  progressPlanStep,
  summarizeTaskSteps,
  generateConclusion,
  openPullRequest,
  diagnoseError,
  requestHelp,
  updatePlan,
} from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";
import { initializeSandbox } from "../shared/initialize-sandbox.js";

/**
 * Routes to the next appropriate node after taking action.
 * If the last message is an AI message with tool calls, it routes to "take-action".
 * Otherwise, it ends the process.
 *
 * @param {GraphState} state - The current graph state.
 * @returns {"open-pr" | "take-action" | "request-help" | Send} The next node to execute, or END if the process should stop.
 */
async function routeGeneratedAction(
  state: GraphState,
): Promise<"open-pr" | "take-action" | "request-help" | Send> {
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
        planChangeRequest: toolCall.args?.update_plan_reasoning,
      });
    }

    return "take-action";
  }

  // No tool calls, create PR then end.
  return "open-pr";
}

const workflow = new StateGraph(GraphAnnotation, GraphConfiguration)
  .addNode("initialize", initializeSandbox)
  .addNode("generate-action", generateAction)
  .addNode("take-action", takeAction, {
    ends: ["progress-plan-step", "diagnose-error"],
  })
  .addNode("update-plan", updatePlan)
  .addNode("progress-plan-step", progressPlanStep, {
    ends: ["summarize-task-steps", "generate-action", "generate-conclusion"],
  })
  .addNode("summarize-task-steps", summarizeTaskSteps, {
    ends: ["generate-action", "generate-conclusion"],
  })
  .addNode("generate-conclusion", generateConclusion)
  .addNode("request-help", requestHelp, {
    ends: ["generate-action", END],
  })
  .addNode("open-pr", openPullRequest)
  .addNode("diagnose-error", diagnoseError)
  .addEdge(START, "initialize")
  .addEdge("initialize", "generate-action")
  .addConditionalEdges("generate-action", routeGeneratedAction, [
    "take-action",
    "request-help",
    "open-pr",
    "update-plan",
  ])
  .addEdge("update-plan", "generate-action")
  .addEdge("generate-conclusion", "open-pr")
  .addEdge("diagnose-error", "generate-action")
  .addEdge("open-pr", END);

// Zod types are messed up
export const graph = workflow.compile() as any;
graph.name = "Open SWE";
