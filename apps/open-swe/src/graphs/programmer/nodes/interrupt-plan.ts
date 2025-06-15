import { Command, END, interrupt } from "@langchain/langgraph";
import { GraphState, GraphUpdate } from "@open-swe/shared/open-swe/types";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { startSandbox } from "../../../utils/sandbox.js";
import { createNewTask } from "@open-swe/shared/open-swe/tasks";
import { getUserRequest } from "../../../utils/user-request.js";
import {
  PLAN_INTERRUPT_ACTION_TITLE,
  PLAN_INTERRUPT_DELIMITER,
} from "@open-swe/shared/constants";

export async function interruptPlan(state: GraphState): Promise<Command> {
  const { proposedPlan } = state;
  if (!proposedPlan.length) {
    throw new Error("No proposed plan found.");
  }

  const interruptRes = interrupt<HumanInterrupt, HumanResponse[]>({
    action_request: {
      action: PLAN_INTERRUPT_ACTION_TITLE,
      args: {
        plan: proposedPlan.join(`\n${PLAN_INTERRUPT_DELIMITER}\n`),
      },
    },
    config: {
      allow_accept: true,
      allow_edit: true,
      allow_respond: true,
      allow_ignore: true,
    },
    description: `A new plan has been generated for your request. Please review it and either approve it, edit it, respond to it, or ignore it. Responses will be passed to an LLM where it will rewrite then plan.
    If editing the plan, ensure each step in the plan is separated by "${PLAN_INTERRUPT_DELIMITER}".`,
  })[0];

  if (!state.sandboxSessionId) {
    // TODO: This should prob just create a sandbox?
    throw new Error("No sandbox session ID found.");
  }

  const userRequest = getUserRequest(state.internalMessages);

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
      .split(PLAN_INTERRUPT_DELIMITER)
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
