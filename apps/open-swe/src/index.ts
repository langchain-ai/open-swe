import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphAnnotation, GraphConfiguration, GraphState } from "./types.js";
import {
  initialize,
  generateAction,
  takeAction,
  rewritePlan,
  interruptPlan,
  progressPlanStep,
  summarizeTaskSteps,
  generateConclusion,
  diagnoseError,
} from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";
import { plannerGraph } from "./subgraphs/index.js";

/**
 * Routes to the next appropriate node after taking action.
 * If the last message is an AI message with tool calls, it routes to "take-action".
 * Otherwise, it ends the process.
 *
 * @param {GraphState} state - The current graph state.
 * @returns {typeof END | "take-action"} The next node to execute, or END if the process should stop.
 */
async function takeActionOrEnd(
  state: GraphState,
): Promise<typeof END | "take-action"> {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];

  // If the message is an AI message, and it has tool calls, we should take action.
  if (isAIMessage(lastMessage) && lastMessage.tool_calls?.length) {
    return "take-action";
  }

  return END;
}

const workflow = new StateGraph(GraphAnnotation, GraphConfiguration)
  .addNode("initialize", initialize)
  .addNode("generate-plan-subgraph", plannerGraph)
  .addNode("rewrite-plan", rewritePlan)
  .addNode("interrupt-plan", interruptPlan, {
    ends: [END, "rewrite-plan", "generate-action"],
  })
  .addNode("generate-action", generateAction)
  .addNode("take-action", takeAction, {
    ends: ["progress-plan-step", "diagnose-error"],
  })
  .addNode("progress-plan-step", progressPlanStep, {
    ends: ["summarize-task-steps", "generate-action", "generate-conclusion"],
  })
  .addNode("summarize-task-steps", summarizeTaskSteps, {
    ends: ["generate-action", "generate-conclusion"],
  })
  .addNode("generate-conclusion", generateConclusion)
  .addNode("diagnose-error", diagnoseError)
  .addEdge(START, "initialize")
  .addEdge("initialize", "generate-plan-subgraph")
  .addEdge("generate-plan-subgraph", "interrupt-plan")
  // Always interrupt after rewriting the plan.
  .addEdge("rewrite-plan", "interrupt-plan")
  .addConditionalEdges("generate-action", takeActionOrEnd, ["take-action", END])
  .addEdge("diagnose-error", "progress-plan-step")
  .addEdge("generate-conclusion", END);

// Zod types are messed up
export const graph = workflow.compile() as any;
graph.name = "Open Codex";
