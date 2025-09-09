import { v4 as uuidv4 } from "uuid";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { Command, END, interrupt } from "@langchain/langgraph";
import { StreamMode } from "@langchain/langgraph-sdk";
import {
  GraphUpdate,
  GraphConfig,
  TaskPlan,
  PlanItem,
} from "@openswe/shared/open-swe/types";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import { createNewTask } from "@openswe/shared/open-swe/tasks";
import {
  getInitialUserRequest,
  getRecentUserRequest,
} from "../../../utils/user-request.js";
import {
  PLAN_INTERRUPT_ACTION_TITLE,
  PLAN_INTERRUPT_DELIMITER,
  DO_NOT_RENDER_ID_PREFIX,
  PROGRAMMER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
  LOCAL_MODE_HEADER,
} from "@openswe/shared/constants";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  ACCEPTED_PLAN_NODE_ID,
  CustomNodeEvent,
} from "@openswe/shared/open-swe/custom-node-events";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { shouldCreateIssue } from "../../../utils/should-create-issue.js";

const logger = createLogger(LogLevel.INFO, "ProposedPlan");

function createAcceptedPlanMessage(input: {
  planTitle: string;
  planItems: PlanItem[];
  interruptType: HumanResponse["type"];
  runId: string;
}) {
  const { planTitle, planItems, interruptType, runId } = input;
  const acceptedPlanEvent: CustomNodeEvent = {
    nodeId: ACCEPTED_PLAN_NODE_ID,
    actionId: uuidv4(),
    action: "Plan accepted",
    createdAt: new Date().toISOString(),
    data: {
      status: "success",
      planTitle,
      planItems,
      interruptType,
      runId,
    },
  };

  const acceptedPlanMessage = new AIMessage({
    id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
    content: "Accepted plan",
    additional_kwargs: {
      hidden: true,
      customNodeEvents: [acceptedPlanEvent],
    },
  });
  return acceptedPlanMessage;
}

async function startProgrammerRun(input: {
  runInput: Exclude<GraphUpdate, "taskPlan"> & { taskPlan: TaskPlan };
  state: PlannerGraphState;
  config: GraphConfig;
  newMessages?: BaseMessage[];
}) {
  const { runInput, state, config, newMessages } = input;
  const isLocal = isLocalMode(config);
  const defaultHeaders: Record<string, string> = isLocal
    ? { [LOCAL_MODE_HEADER]: "true" }
    : {};

  const langGraphClient = createLangGraphClient({
    defaultHeaders,
  });

  const programmerThreadId = uuidv4();
  // Restart the sandbox.
  const { sandbox, codebaseTree, dependenciesInstalled } =
    await getSandboxWithErrorHandling(
      state.sandboxSessionId,
      state.targetRepository,
      state.branchName,
      config,
    );
  runInput.sandboxSessionId = sandbox.id;
  runInput.codebaseTree = codebaseTree ?? runInput.codebaseTree;
  runInput.dependenciesInstalled =
    dependenciesInstalled !== null
      ? dependenciesInstalled
      : runInput.dependenciesInstalled;

  const run = await langGraphClient.runs.create(
    programmerThreadId,
    PROGRAMMER_GRAPH_ID,
    {
      input: runInput,
      config: {
        recursion_limit: 400,
        configurable: {
          ...getCustomConfigurableFields(config),
          ...(isLocalMode(config) && { [LOCAL_MODE_HEADER]: "true" }),
        },
      },
      ifNotExists: "create",
      streamResumable: true,
      streamSubgraphs: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    },
  );

  if (!isLocalMode(config) && shouldCreateIssue(config)) {
    logger.info("Skipping remote issue update: not supported");
  }

  return new Command({
    goto: END,
    update: {
      programmerSession: {
        threadId: programmerThreadId,
        runId: run.run_id,
      },
      sandboxSessionId: runInput.sandboxSessionId,
      taskPlan: runInput.taskPlan,
      messages: newMessages,
    },
  });
}

export async function interruptProposedPlan(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const { proposedPlan } = state;
  if (!proposedPlan.length) {
    throw new Error("No proposed plan found.");
  }

  logger.info("Interrupting proposed plan", {
    autoAcceptPlan: state.autoAcceptPlan,
    isLocalMode: isLocalMode(config),
    proposedPlanLength: proposedPlan.length,
    proposedPlanTitle: state.proposedPlanTitle,
  });

  let planItems: PlanItem[];
  const userRequest = getInitialUserRequest(state.messages);
  const userFollowupRequest = getRecentUserRequest(state.messages);
  const userTaskRequest = userFollowupRequest || userRequest;
  const runInput: GraphUpdate = {
    contextGatheringNotes: state.contextGatheringNotes,
    branchName: state.branchName,
    targetRepository: state.targetRepository,
    issueId: state.issueId,
    internalMessages: state.messages,
    documentCache: state.documentCache,
  };

  if (state.autoAcceptPlan) {
    logger.info("Auto accepting plan.", {
      autoAcceptPlan: state.autoAcceptPlan,
      isLocalMode: isLocalMode(config),
    });

    // Post comment to issue about auto-accepting the plan (only if not in local mode)
    if (!isLocalMode(config) && state.issueId) {
      logger.info("Plan generated; skipping remote issue comment");
    }

    planItems = proposedPlan.map((p, index) => ({
      index,
      plan: p,
      completed: false,
    }));
    runInput.taskPlan = createNewTask(
      userTaskRequest,
      state.proposedPlanTitle,
      planItems,
      { existingTaskPlan: state.taskPlan },
    );

    return await startProgrammerRun({
      runInput: runInput as Exclude<GraphUpdate, "taskPlan"> & {
        taskPlan: TaskPlan;
      },
      state,
      config,
      newMessages: [
        createAcceptedPlanMessage({
          planTitle: state.proposedPlanTitle,
          planItems,
          interruptType: "accept",
          runId: (config.configurable?.run_id as string | undefined) ?? "",
        }),
      ],
    });
  }

  if (!isLocalMode(config) && state.issueId) {
    logger.info("Plan ready for approval; skipping remote issue comment");
  }

  const interruptResponse = interrupt<
    HumanInterrupt,
    HumanResponse[] | HumanResponse
  >({
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
  });

  const humanResponse: HumanResponse = Array.isArray(interruptResponse)
    ? interruptResponse[0]
    : interruptResponse;

  if (humanResponse.type === "response") {
    // Plan was responded to, route to the needs-context node which will determine
    // if we need more context, or can go right to the planning step.
    return new Command({
      goto: "determine-needs-context",
    });
  }

  if (humanResponse.type === "ignore") {
    // Plan was ignored, end the process.
    return new Command({
      goto: END,
    });
  }

  if (humanResponse.type === "accept") {
    planItems = proposedPlan.map((p, index) => ({
      index,
      plan: p,
      completed: false,
    }));

    runInput.taskPlan = createNewTask(
      userTaskRequest,
      state.proposedPlanTitle,
      planItems,
      { existingTaskPlan: state.taskPlan },
    );

    // Update the comment to notify the user that the plan was accepted (only if not in local mode)
    if (!isLocalMode(config) && state.issueId) {
      logger.info("Plan accepted; skipping remote issue comment");
    }
  } else if (humanResponse.type === "edit") {
    const editedPlan = (humanResponse.args as ActionRequest).args.plan
      .split(PLAN_INTERRUPT_DELIMITER)
      .map((step: string) => step.trim());

    planItems = editedPlan.map((p: string, index: number) => ({
      index,
      plan: p,
      completed: false,
    }));

    runInput.taskPlan = createNewTask(
      userTaskRequest,
      state.proposedPlanTitle,
      planItems,
      { existingTaskPlan: state.taskPlan },
    );

    // Update the comment to notify the user that the plan was edited (only if not in local mode)
    if (!isLocalMode(config) && state.issueId) {
      logger.info("Plan edited and submitted; skipping remote issue comment");
    }
  } else {
    throw new Error("Unknown interrupt type." + humanResponse.type);
  }

  return await startProgrammerRun({
    runInput: runInput as Exclude<GraphUpdate, "taskPlan"> & {
      taskPlan: TaskPlan;
    },
    state,
    config,
    newMessages: [
      createAcceptedPlanMessage({
        planTitle: state.proposedPlanTitle,
        planItems,
        interruptType: humanResponse.type,
        runId: (config.configurable?.run_id as string | undefined) ?? "",
      }),
    ],
  });
}
