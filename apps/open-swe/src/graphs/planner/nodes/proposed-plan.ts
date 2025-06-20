import { v4 as uuidv4 } from "uuid";
import { Command, END, interrupt } from "@langchain/langgraph";
import { GraphUpdate, GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { startSandbox } from "../../../utils/sandbox.js";
import { createNewTask } from "@open-swe/shared/open-swe/tasks";
import { getUserRequest } from "../../../utils/user-request.js";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
  PLAN_INTERRUPT_ACTION_TITLE,
  PLAN_INTERRUPT_DELIMITER,
} from "@open-swe/shared/constants";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import { addTaskPlanToIssue } from "../../../utils/github/issue-task.js";

export async function interruptProposedPlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate | Command> {
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

  if (interruptRes.type === "response") {
    // Plan was responded to, route to the rewrite plan node.
    throw new Error("RESPONDING TO PLAN NOT IMPLEMENTED.");
  }

  if (interruptRes.type === "ignore") {
    // Plan was ignored, end the process.
    return new Command({
      goto: END,
    });
  }

  const langGraphClient = createLangGraphClient({
    defaultHeaders: {
      [GITHUB_TOKEN_COOKIE]: config.configurable?.[GITHUB_TOKEN_COOKIE] ?? "",
      [GITHUB_INSTALLATION_TOKEN_COOKIE]:
        config.configurable?.[GITHUB_INSTALLATION_TOKEN_COOKIE] ?? "",
    },
  });

  const userRequest = getUserRequest(state.messages);

  const runInput: GraphUpdate = {
    contextGatheringNotes: state.contextGatheringNotes,
    branchName: state.branchName,
    targetRepository: state.targetRepository,
    githubIssueId: state.githubIssueId,
  };
  // TODO: UPDATE ISSUE WITH PROGRAMMER THREAD ID.
  // TODO: UPDATE ISSUE WITH TASK PLAN
  const programmerThreadId = uuidv4();

  if (interruptRes.type === "accept") {
    const planItems = proposedPlan.map((p, index) => ({
      index,
      plan: p,
      completed: false,
    }));

    runInput.taskPlan = createNewTask(userRequest, planItems, state.taskPlan);
  } else if (interruptRes.type === "edit") {
    const editedPlan = (interruptRes.args as ActionRequest).args.plan
      .split(PLAN_INTERRUPT_DELIMITER)
      .map((step: string) => step.trim());

    const planItems = editedPlan.map((p: string, index: number) => ({
      index,
      plan: p,
      completed: false,
    }));

    runInput.taskPlan = createNewTask(userRequest, planItems, state.taskPlan);
  } else {
    throw new Error("Unknown interrupt type." + interruptRes.type);
  }

  // Restart the sandbox.
  runInput.sandboxSessionId = (await startSandbox(state.sandboxSessionId)).id;

  const run = await langGraphClient.runs.create(
    programmerThreadId,
    "programmer",
    {
      input: runInput,
      config: {
        recursion_limit: 400,
      },
      ifNotExists: "create",
      streamResumable: true,
      streamMode: ["values", "messages", "custom"],
    },
  );

  await addTaskPlanToIssue(
    {
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
    },
    config,
    runInput.taskPlan,
  );

  return {
    programmerSession: {
      threadId: programmerThreadId,
      runId: run.run_id,
    },
    sandboxSessionId: runInput.sandboxSessionId,
    taskPlan: runInput.taskPlan,
  };
}
