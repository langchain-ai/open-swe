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
} from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";
import { plannerGraph } from "./subgraphs/index.js";

/**
 * @param {GraphState} state - The current graph state.
 * @returns {"interrupt-plan" | typeof END} The next node to execute, or END if the process should stop.
 */
function routeAfterPlan(state: GraphState): "interrupt-plan" | typeof END {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];
  if (isAIMessage(lastMessage) && !lastMessage.tool_calls) {
    // The last message is an AI message without tool calls. This indicates the LLM generated followup questions.
    return END;
  }

  return "interrupt-plan";
}

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
    // TODO: Hookup `Command` in interruptPlan node so this actually works.
    ends: [END, "rewrite-plan", "generate-action"],
  })
  .addNode("generate-action", generateAction)
  .addNode("take-action", takeAction)
  .addNode("progress-plan-step", progressPlanStep, {
    ends: ["summarize-task-steps", "generate-action"],
  })
  .addNode("summarize-task-steps", summarizeTaskSteps)
  .addEdge(START, "initialize")
  .addEdge("initialize", "generate-plan-subgraph")
  // TODO: Update routing to work w/ new interrupt node.
  .addConditionalEdges("generate-plan-subgraph", routeAfterPlan, [
    "interrupt-plan",
    END,
  ])
  // Always interrupt after rewriting the plan.
  .addEdge("rewrite-plan", "interrupt-plan")
  .addConditionalEdges("generate-action", takeActionOrEnd, ["take-action", END])
  .addEdge("take-action", "progress-plan-step")
  .addEdge("summarize-task-steps", "generate-action");

// Zod types are messed up
export const graph = workflow.compile() as any;
graph.name = "Open Codex";
