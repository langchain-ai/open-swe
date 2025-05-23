/**
 * Subgraph for gathering context & generating a plan.
 * pre-requisites:
 * VM is already booted & repo is cloned (init node)
 * Steps:
 */

import { END, Send, START, StateGraph } from "@langchain/langgraph";
import { PlannerGraphState, PlannerGraphStateObj } from "./types.js";
import { GraphConfiguration } from "../../types.js";
import { generateAction, generatePlan, takeAction } from "./nodes/index.js";
import { isAIMessage, ToolMessage } from "@langchain/core/messages";

function takeActionOrGeneratePlan(
  state: PlannerGraphState,
): "take-plan-action" | "generate-plan" | Send {
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

  if (isAIMessage(lastMessage) && lastMessage.tool_calls?.length) {
    // If this is true, we need to return a `Command` adding a ToolMessage to the state
    // so that the last AI message has a tool message pair
    const lastMessageToolCall = lastMessage.tool_calls[0];
    return new Send("generate-plan", {
      ...state,
      plannerMessages: [
        ...state.plannerMessages,
        new ToolMessage({
          tool_call_id: lastMessageToolCall.id ?? "",
          name: lastMessageToolCall.name,
          content: "Tool call not executed. Max actions reached.",
        }),
      ],
    });
  }

  // If the last message does not have tool calls, continue to generate plan without modifications.
  return "generate-plan";
}

const workflow = new StateGraph(PlannerGraphStateObj, GraphConfiguration)
  .addNode("generate-plan-context-action", generateAction)
  .addNode("take-plan-action", takeAction)
  .addNode("generate-plan", generatePlan)
  .addEdge(START, "generate-plan-context-action")
  .addConditionalEdges(
    "generate-plan-context-action",
    takeActionOrGeneratePlan,
    ["take-plan-action", "generate-plan"],
  )
  .addEdge("take-plan-action", "generate-plan-context-action")
  .addEdge("generate-plan", END);

// TODO: Fix zod types
export const plannerGraph = workflow.compile() as any;
plannerGraph.name = "Planner";
