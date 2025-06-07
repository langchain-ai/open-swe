import { Command, END, interrupt } from "@langchain/langgraph";
import { GraphState, GraphUpdate } from "../types.js";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { startSandbox } from "../utils/sandbox.js";
import { createNewTask } from "../utils/task-plan.js";
import { getUserRequest } from "../utils/user-request.js";

export async function interruptPlan(state: GraphState): Promise<Command> {
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

  if (!state.sandboxSessionId) {
    // TODO: This should prob just create a sandbox?
    throw new Error("No sandbox session ID found.");
  }

  const userRequest = getUserRequest(state.messages);

  if (interruptRes.type === "accept") {
    const newSandboxSessionId = (await startSandbox(state.sandboxSessionId)).id;

    // Plan was accepted, route to the generate-action node to start taking actions.
    const planItems = proposedPlan.map((p, index) => ({
      index,
      plan: p,
      completed: false,
    }));

    const newTaskPlan = createNewTask(userRequest, planItems, state.plan);

    const commandUpdate: GraphUpdate = {
      plan: newTaskPlan,
      sandboxSessionId: newSandboxSessionId,
    };
    return new Command({
      goto: "generate-action",
      update: commandUpdate,
    });
  }

  if (interruptRes.type === "edit") {
    const newSandboxSessionId = (await startSandbox(state.sandboxSessionId)).id;

    // Plan was edited, route to the generate-action node to start taking actions.
    const editedPlan = (interruptRes.args as ActionRequest).args.plan
      .split(":::")
      .map((step: string) => step.trim());

    const planItems = editedPlan.map((p: string, index: number) => ({
      index,
      plan: p,
      completed: false,
    }));

    const newTaskPlan = createNewTask(userRequest, planItems, state.plan);

    const commandUpdate: GraphUpdate = {
      plan: newTaskPlan,
      sandboxSessionId: newSandboxSessionId,
    };
    return new Command({
      goto: "generate-action",
      update: commandUpdate,
    });
  }

  if (interruptRes.type === "response") {
    // Plan was responded to, route to the rewrite plan node.
    const commandUpdate: GraphUpdate = {
      planChangeRequest: interruptRes.args as string,
    };
    return new Command({
      goto: "rewrite-plan",
      update: commandUpdate,
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
