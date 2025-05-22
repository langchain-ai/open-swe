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
import { isAIMessage } from "@langchain/core/messages";

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
  .addNode("generate-plan", generatePlan)
  .addNode("rewrite-plan", rewritePlan)
  .addNode("interrupt-plan", interruptPlan, {
    // TODO: Hookup `Command` in interruptPlan node so this actually works.
    ends: [END, "rewrite-plan", "initialize"],
  })
  .addNode("initialize", initialize)
  .addNode("generate-action", generateAction)
  .addNode("take-action", takeAction)
  .addEdge(START, "generate-plan")
  // TODO: Update routing to work w/ new interrupt node.
  .addConditionalEdges("generate-plan", routeAfterPlan, ["interrupt-plan", END])
  // Always interrupt after rewriting the plan.
  .addEdge("rewrite-plan", "interrupt-plan")
  .addEdge("initialize", "generate-action")
  .addEdge("generate-plan", "generate-action")
  .addConditionalEdges("generate-action", takeActionOrEnd, ["take-action", END])
  .addEdge("take-action", "generate-action");

export const graph = workflow.compile();
graph.name = "LangGraph ReAct MCP";
