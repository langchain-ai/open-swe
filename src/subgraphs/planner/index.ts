/**
 * Subgraph for gathering context & generating a plan.
 * pre-requisites:
 * VM is already booted & repo is cloned (init node)
 * Steps:
 */

import { END, START, StateGraph } from "@langchain/langgraph";
import { PlannerGraphState, PlannerGraphStateObj } from "./types.js";
import { GraphConfiguration } from "../../types.js";
import {
  generateAction,
  generatePlan,
  summarizer,
  takeAction,
} from "./nodes/index.js";
import { isAIMessage } from "@langchain/core/messages";

function takeActionOrGeneratePlan(
  state: PlannerGraphState,
): "take-plan-action" | "generate-plan" {
  const { plannerMessages } = state;
  const lastMessage = plannerMessages[plannerMessages.length - 1];
  // If the last message is a tool call, and we have executed less than 6 actions, take action.
  // Max actions is 13, because that's 6 actions (2 messages per action, ai & tool) plus the input message.
  const maxActionsCount = 13;
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

const workflow = new StateGraph(PlannerGraphStateObj, GraphConfiguration)
  .addNode("generate-plan-context-action", generateAction)
  .addNode("take-plan-action", takeAction)
  .addNode("generate-plan", generatePlan)
  .addNode("summarizer", summarizer)
  .addEdge(START, "generate-plan-context-action")
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
