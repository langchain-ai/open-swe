/**
 * Subgraph for gathering context & generating a plan.
 * pre-requisites:
 * VM is already booted & repo is cloned (init node)
 * Steps:
 */

import { END, START, StateGraph } from "@langchain/langgraph";
import {
  PlannerGraphState,
  PlannerGraphStateObj,
  PlannerGraphUpdate,
} from "./types.js";
import { GraphConfig, GraphConfiguration } from "../../types.js";
import {
  generateAction,
  generatePlan,
  summarizer,
  takeAction,
} from "./nodes/index.js";
import { isAIMessage, RemoveMessage } from "@langchain/core/messages";

function takeActionOrGeneratePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): "take-plan-action" | "generate-plan" {
  const { plannerMessages } = state;
  const lastMessage = plannerMessages[plannerMessages.length - 1];
  // If the last message is a tool call, and we have executed less than 6 actions, take action.
  // Max actions count is calculated as: maxContextActions * 2 + 1
  // This is because each action generates 2 messages (AI request + tool result) plus 1 initial human message
  const maxContextActions = config.configurable?.maxContextActions ?? 6;
  const maxActionsCount = maxContextActions * 2 + 1;
  if (
    isAIMessage(lastMessage) &&
    lastMessage.tool_calls?.length &&
    plannerMessages.length < maxActionsCount
  ) {
    return "take-plan-action";
  }

  // If the last message does not have tool calls, continue to generate plan without modifications.
  return "generate-plan";
}

function prepareGraphState(state: PlannerGraphState): PlannerGraphUpdate {
  if (!state.plannerMessages?.length) return {};
  // Remove all planning messages if we're starting a new plan
  return {
    plannerMessages: state.plannerMessages.map(
      (m) => new RemoveMessage({ id: m.id ?? "" }),
    ),
  };
}

const workflow = new StateGraph(PlannerGraphStateObj, GraphConfiguration)
  .addNode("prepare-graph-state", prepareGraphState)
  .addNode("generate-plan-context-action", generateAction)
  .addNode("take-plan-action", takeAction)
  .addNode("generate-plan", generatePlan)
  .addNode("summarizer", summarizer)
  .addEdge(START, "prepare-graph-state")
  .addEdge("prepare-graph-state", "generate-plan-context-action")
  .addConditionalEdges(
    "generate-plan-context-action",
    takeActionOrGeneratePlan,
    ["take-plan-action", "generate-plan"],
  )
  .addEdge("take-plan-action", "generate-plan-context-action")
  .addEdge("generate-plan", "summarizer")
  .addEdge("summarizer", END);

// TODO: Fix zod types
export const plannerGraph = workflow.compile() as any;
plannerGraph.name = "Planner";
