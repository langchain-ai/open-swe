import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphAnnotation, GraphConfiguration, GraphState } from "./types.js";
import {
  generatePlan,
  initialize,
  generateAction,
  takeAction,
  rewritePlan,
  interruptPlan,
} from "./nodes/index.js";
import { isAIMessage, isToolMessage } from "@langchain/core/messages";

/**
 * Determines the next step after a plan has been potentially rewritten.
 * It checks if the latest plan was approved.
 * If an approved "session_plan" tool message exists, it routes to "generate-action".
 * Otherwise, it routes to "rewrite-plan" to revise the plan.
 *
 * @param {GraphState} state - The current graph state.
 * @returns {"generate-action" | "rewrite-plan"} The next node to execute.
 */
function routeAfterRewritingPlan(
  state: GraphState,
): "generate-action" | "rewrite-plan" {
  const { messages } = state;

  // TODO: THIS WILL CAUSE ISSUES IF WE ALLOW FOR FOLLOWUP REQUESTS
  // Search for a tool message responding to the "session_plan" tool call where the content is "approved"
  const planApprovedMessage = messages.find(
    (m) =>
      isToolMessage(m) && m.name === "session_plan" && m.content === "approved",
  );
  if (planApprovedMessage) {
    return "generate-action";
  }
  // If this does not exist, we should rewrite the plan.
  return "rewrite-plan";
}

/**
 * Routes to the next appropriate node after a plan has been generated or attempted.
 * If the last message is an AI message without tool calls (indicating a follow-up question or direct answer),
 * the process ends.
 * Otherwise, it delegates to `routeAfterRewritingPlan` to check for plan approval and decide
 * whether to generate an action or rewrite the plan.
 *
 * @param {GraphState} state - The current graph state.
 * @returns {"generate-action" | "rewrite-plan" | typeof END} The next node to execute, or END if the process should stop.
 */
function routeAfterPlan(
  state: GraphState,
): "generate-action" | "rewrite-plan" | typeof END {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];
  if (isAIMessage(lastMessage) && !lastMessage.tool_calls) {
    // The last message is an AI message without tool calls. This indicates the LLM generated followup questions.
    return END;
  }

  return routeAfterRewritingPlan(state);
}

/**
 * Routes to the next appropriate node after taking action.
 * If the last message is an AI message with tool calls, it routes to "take-action".
 * Otherwise, it ends the process.
 *
 * @param {GraphState} state - The current graph state.
 * @returns {typeof END | "take-action"} The next node to execute, or END if the process should stop.
 */
function takeActionOrEnd(state: GraphState): typeof END | "take-action" {
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
  .addNode("generate-plan", generatePlan)
  .addNode("rewrite-plan", rewritePlan)
  .addNode("interrupt-plan", interruptPlan, {
    // TODO: Hookup `Command` in interruptPlan node so this actually works.
    ends: [END, "rewrite-plan", "generate-action"],
  })
  .addNode("generate-action", generateAction)
  .addNode("take-action", takeAction)
  .addEdge(START, "initialize")
  .addEdge("initialize", "generate-plan")
  // TODO: Update routing to work w/ new interrupt node.
  .addConditionalEdges("generate-plan", routeAfterPlan, ["interrupt-plan", END])
  .addConditionalEdges("rewrite-plan", routeAfterRewritingPlan, [
    "generate-action",
    "interrupt-plan",
  ])
  .addEdge("generate-plan", "generate-action")
  .addConditionalEdges("generate-action", takeActionOrEnd, ["take-action", END])
  .addEdge("take-action", "generate-action");

export const graph = workflow.compile();
graph.name = "LangGraph ReAct MCP";
