import { v4 as uuidv4 } from "uuid";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { Command, END, interrupt } from "@langchain/langgraph";
import { StreamMode } from "@langchain/langgraph-sdk";
import {
  GraphUpdate,
  GraphConfig,
  TaskPlan,
  PlanItem,
} from "@open-swe/shared/open-swe/types";
import {
  ActionRequest,
  HumanInterrupt,
  HumanResponse,
} from "@langchain/langgraph/prebuilt";
import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import { createNewTask } from "@open-swe/shared/open-swe/tasks";
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
} from "@open-swe/shared/constants";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import {
  addProposedPlanToIssue,
  addTaskPlanToIssue,
} from "../../../utils/github/issue-task.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  ACCEPTED_PLAN_NODE_ID,
  CustomNodeEvent,
} from "@open-swe/shared/open-swe/custom-node-events";
import { getDefaultHeaders } from "../../../utils/default-headers.js";
import { getCustomConfigurableFields } from "../../../utils/config.js";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import {
  createIssueComment,
  getIssueComments,
  updateIssueComment,
} from "../../../utils/github/api.js";

const logger = createLogger(LogLevel.INFO, "ProposedPlan");

const PLAN_MESSAGE_OPEN_TAG = "<open-swe-plan-message>";
const PLAN_MESSAGE_CLOSE_TAG = "</open-swe-plan-message>";

function formatBodyWithPlanMessage(body: string, message: string): string {
  if (
    body.includes(PLAN_MESSAGE_OPEN_TAG) &&
    body.includes(PLAN_MESSAGE_CLOSE_TAG)
  ) {
    const bodyBeforeTag = body.split(PLAN_MESSAGE_OPEN_TAG)[0];
    const bodyAfterTag = body.split(PLAN_MESSAGE_CLOSE_TAG)[1];
    const newInnerContents = `\n${PLAN_MESSAGE_OPEN_TAG}\n\n${message}\n\n${PLAN_MESSAGE_CLOSE_TAG}\n`;
    return `${bodyBeforeTag}${newInnerContents}${bodyAfterTag}`;
  }

  return `${body}\n${PLAN_MESSAGE_OPEN_TAG}\n\n${message}\n\n${PLAN_MESSAGE_CLOSE_TAG}`;
}

function cleanTaskItems(taskItem: string): string {
  return "```\n" + taskItem.replace("```", "\\```") + "\n```";
}

/**
 * Posts a comment to a GitHub issue using the installation token
 */
export async function postGitHubIssueComment(input: {
  githubIssueId: number;
  targetRepository: { owner: string; repo: string };
  commentBody: string;
  config: GraphConfig;
}): Promise<void> {
  const { githubIssueId, targetRepository, commentBody, config } = input;
  const githubAppName = process.env.GITHUB_APP_NAME;
  if (!githubAppName) {
    throw new Error("GITHUB_APP_NAME not set");
  }

  try {
    const { githubInstallationToken } = getGitHubTokensFromConfig(config);
    const existingComments = await getIssueComments({
      owner: targetRepository.owner,
      repo: targetRepository.repo,
      issueNumber: githubIssueId,
      githubInstallationToken,
      filterBotComments: false,
    });

    const existingOpenSWEComment = existingComments?.findLast((c) =>
      c.user?.login?.toLowerCase()?.startsWith(githubAppName.toLowerCase()),
    );

    if (!existingOpenSWEComment) {
      await createIssueComment({
        owner: targetRepository.owner,
        repo: targetRepository.repo,
        issueNumber: githubIssueId,
        body: commentBody,
        githubToken: githubInstallationToken,
      });

      logger.info(`Posted comment to GitHub issue #${githubIssueId}`);
      return;
    }

    // Update the comment
    const newCommentBody = formatBodyWithPlanMessage(
      existingOpenSWEComment.body ?? "",
      commentBody,
    );
    await updateIssueComment({
      owner: targetRepository.owner,
      repo: targetRepository.repo,
      commentId: existingOpenSWEComment.id,
      body: newCommentBody,
      githubInstallationToken,
    });

    logger.info(`Updated comment to GitHub issue #${githubIssueId}`);
  } catch (error) {
    logger.error("Failed to post GitHub comment:", error);
    // Don't throw - we don't want to fail the entire process if comment posting fails
  }
}

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
  const langGraphClient = createLangGraphClient({
    defaultHeaders: getDefaultHeaders(config),
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
        configurable: getCustomConfigurableFields(config),
      },
      ifNotExists: "create",
      streamResumable: true,
      streamSubgraphs: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
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

  let planItems: PlanItem[];
  const userRequest = getInitialUserRequest(state.messages);
  const userFollowupRequest = getRecentUserRequest(state.messages);
  const userTaskRequest = userFollowupRequest || userRequest;
  const runInput: GraphUpdate = {
    contextGatheringNotes: state.contextGatheringNotes,
    branchName: state.branchName,
    targetRepository: state.targetRepository,
    githubIssueId: state.githubIssueId,
    internalMessages: state.messages,
    documentCache: state.documentCache,
  };

  if (state.autoAcceptPlan) {
    logger.info("Auto accepting plan.");

    // Post comment to GitHub issue about auto-accepting the plan
    await postGitHubIssueComment({
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
      commentBody: `### 🤖 Plan Generated\n\nI've generated a plan for this issue and will proceed to implement it since auto-accept is enabled.\n\n**Plan: ${state.proposedPlanTitle}**\n\n${proposedPlan.map((step, index) => `- Task ${index + 1}:\n${cleanTaskItems(step)}`).join("\n")}\n\nProceeding to implementation...`,
      config,
    });

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
          runId: config.configurable?.run_id ?? "",
        }),
      ],
    });
  }

  await addProposedPlanToIssue(
    {
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
    },
    config,
    proposedPlan,
  );

  // Post comment to GitHub issue about plan being ready for approval
  await postGitHubIssueComment({
    githubIssueId: state.githubIssueId,
    targetRepository: state.targetRepository,
    commentBody: `### 🟠 Plan Ready for Approval 🟠\n\nI've generated a plan for this issue and it's ready for your review.\n\n**Plan: ${state.proposedPlanTitle}**\n\n${proposedPlan.map((step, index) => `- Task ${index + 1}:\n${cleanTaskItems(step)}`).join("\n")}\n\nPlease review the plan and let me know if you'd like me to proceed, make changes, or if you have any feedback.`,
    config,
  });

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

    // Update the comment to notify the user that the plan was accepted
    await postGitHubIssueComment({
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
      commentBody: `### ✅ Plan Accepted ✅\n\nThe proposed plan was accepted.\n\n**Plan: ${state.proposedPlanTitle}**\n\n${planItems.map((step, index) => `- Task ${index + 1}:\n${cleanTaskItems(step.plan)}`).join("\n")}\n\nProceeding to implementation...`,
      config,
    });
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

    await postGitHubIssueComment({
      githubIssueId: state.githubIssueId,
      targetRepository: state.targetRepository,
      commentBody: `### ✅ Plan Edited & Submitted ✅\n\nThe proposed plan was edited and submitted.\n\n**Plan: ${state.proposedPlanTitle}**\n\n${planItems.map((step, index) => `- Task ${index + 1}:\n${cleanTaskItems(step.plan)}`).join("\n")}\n\nProceeding to implementation...`,
      config,
    });
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
        runId: config.configurable?.run_id ?? "",
      }),
    ],
  });
}
