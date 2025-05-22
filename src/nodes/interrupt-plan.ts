import { interrupt } from "@langchain/langgraph";
import { GraphState, GraphUpdate } from "../types.js";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { v4 as uuidv4 } from "uuid";

export function interruptPlan(state: GraphState): GraphUpdate {
  const { proposedPlan } = state;
  if (!proposedPlan.length) {
    throw new Error("No proposed plan found.");
  }

  const interruptRes = interrupt<HumanInterrupt, HumanResponse[]>({
    action_request: {
      action: "Approve/Edit Plan",
      args: {
        plan: proposedPlan.join("\n:::\n"),
      },
    },
    config: {
      allow_accept: true,
      allow_edit: true,
      allow_respond: true,
      allow_ignore: true,
    },
    description: `A new plan has been generated for your request. Please review it and either approve it, edit it, respond to it, or ignore it. Responses will be passed to an LLM where it will rewrite then plan.
    If editing the plan, ensure each step in the plan is separated by ":::".`,
  })[0];

  if (interruptRes.type === "accept") {
    // Plan was accepted, return it as is.
    return {
      plan: proposedPlan.map((p) => ({
        id: uuidv4(),
        plan: p,
        completed: false,
      })),
    };
  }

  if (interruptRes.type === "edit") {
    // Plan was edited, return the edited plan.
    const editedPlan = (interruptRes.args as ActionRequest).args.plan
      .split(":::")
      .map((step: string) => step.trim());
    return {
      plan: editedPlan.map((p: string) => ({
        id: uuidv4(),
        plan: p,
        completed: false,
      })),
    };
  }

  if (interruptRes.type === "response") {
    // Plan was responded to, return the user's response as a new message.
    return {
      messages: { role: "user", content: interruptRes.args as string },
    };
  }

  if (interruptRes.type === "ignore") {
    throw new Error("Plan was ignored. Session will end.");
  }

  throw new Error("Unknown interrupt type." + interruptRes.type);
}
