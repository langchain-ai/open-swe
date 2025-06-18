import { END, START, StateGraph } from "@langchain/langgraph";
import { PlannerGraphState, PlannerGraphStateObj } from "./types.js";
import {
  GraphConfig,
  GraphConfiguration,
} from "@open-swe/shared/open-swe/types";
import {
  generateAction,
  generatePlan,
  interruptProposedPlan,
  prepareGraphState,
  summarizer,
  takeAction,
} from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";
import { initializeSandbox } from "../shared/initialize-sandbox.js";

function takeActionOrGeneratePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): "take-plan-action" | "generate-plan" {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];
  // If the last message is a tool call, and we have executed less than 6 actions, take action.
  // Max actions count is calculated as: maxContextActions * 2 + 1
  // This is because each action generates 2 messages (AI request + tool result) plus 1 initial human message
  const maxContextActions = config.configurable?.maxContextActions ?? 6;
  const maxActionsCount = maxContextActions * 2 + 1;
  if (
    isAIMessage(lastMessage) &&
    lastMessage.tool_calls?.length &&
    messages.length < maxActionsCount
  ) {
    return "take-plan-action";
  }

  // If the last message does not have tool calls, continue to generate plan without modifications.
  return "generate-plan";
}

const workflow = new StateGraph(PlannerGraphStateObj, GraphConfiguration)
  .addNode("prepare-graph-state", prepareGraphState, {
    ends: [END, "initialize-sandbox"],
  })
  .addNode("initialize-sandbox", initializeSandbox)
  .addNode("generate-plan-context-action", generateAction)
  .addNode("take-plan-action", takeAction)
  .addNode("generate-plan", generatePlan)
  .addNode("summarizer", summarizer)
  .addNode("interrupt-proposed-plan", interruptProposedPlan)
  .addEdge(START, "prepare-graph-state")
  .addEdge("initialize-sandbox", "generate-plan-context-action")
  .addConditionalEdges(
    "generate-plan-context-action",
    takeActionOrGeneratePlan,
    ["take-plan-action", "generate-plan"],
  )
  .addEdge("take-plan-action", "generate-plan-context-action")
  .addEdge("generate-plan", "summarizer")
  .addEdge("summarizer", "interrupt-proposed-plan")
  .addEdge("interrupt-proposed-plan", END);

export const graph = workflow.compile();
graph.name = "Open SWE - Planner";
