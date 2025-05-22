import { Command, END, interrupt } from "@langchain/langgraph";
import { GraphState } from "../types.js";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { v4 as uuidv4 } from "uuid";

export function interruptPlan(state: GraphState): Command {
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
    // Plan was accepted, route to the initialize node.
    return new Command({
      goto: "initialize",
      update: {
        plan: proposedPlan.map((p) => ({
          id: uuidv4(),
          plan: p,
          completed: false,
        })),
      },
    });
  }

  if (interruptRes.type === "edit") {
    // Plan was edited, route to the initialize node.
    const editedPlan = (interruptRes.args as ActionRequest).args.plan
      .split(":::")
      .map((step: string) => step.trim());
    return new Command({
      goto: "initialize",
      update: {
        plan: editedPlan.map((p: string) => ({
          id: uuidv4(),
          plan: p,
          completed: false,
        })),
      },
    });
  }

  if (interruptRes.type === "response") {
    // Plan was responded to, route to the rewrite plan node.
    return new Command({
      goto: "rewrite-plan",
      update: {
        planChangeRequest: interruptRes.args as string,
      },
    });
  }

  if (interruptRes.type === "ignore") {
    // Plan was ignored, end the process.
    return new Command({
      goto: END,
    });
  }

  throw new Error("Unknown interrupt type." + interruptRes.type);
}
