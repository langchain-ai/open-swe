import {
  GraphConfig,
  TargetRepository,
  TaskPlan,
} from "@open-swe/shared/open-swe/types";
import { getIssue, updateIssue } from "./api.js";
import { getGitHubTokensFromConfig } from "../github-tokens.js";
import { createLogger, LogLevel } from "../logger.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
const logger = createLogger(LogLevel.INFO, "IssueTaskString");

export const TASK_OPEN_TAG = "<open-swe-do-not-edit-task-plan>";
export const TASK_CLOSE_TAG = "</open-swe-do-not-edit-task-plan>";

export const PROPOSED_PLAN_OPEN_TAG = "<open-swe-do-not-edit-proposed-plan>";
export const PROPOSED_PLAN_CLOSE_TAG = "</open-swe-do-not-edit-proposed-plan>";

export const DETAILS_OPEN_TAG = "<details>";
export const DETAILS_CLOSE_TAG = "</details>";
const AGENT_CONTEXT_DETAILS_SUMMARY = "<summary>Agent Context</summary>";

function typeNarrowTaskPlan(taskPlan: unknown): taskPlan is TaskPlan {
  return !!(
    typeof taskPlan === "object" &&
    !Array.isArray(taskPlan) &&
    taskPlan &&
    "tasks" in taskPlan &&
    Array.isArray(taskPlan.tasks) &&
    "activeTaskIndex" in taskPlan &&
    typeof taskPlan.activeTaskIndex === "number"
  );
}

export function extractTasksFromIssueContent(content: string): TaskPlan | null {
  if (!content.includes(TASK_OPEN_TAG) || !content.includes(TASK_CLOSE_TAG)) {
    return null;
  }
  const taskPlanString = content
    .split(TASK_OPEN_TAG)?.[1]
    ?.split(TASK_CLOSE_TAG)?.[0];
  try {
    const parsedTaskPlan = JSON.parse(taskPlanString.trim());
    if (!typeNarrowTaskPlan(parsedTaskPlan)) {
      throw new Error("Invalid task plan parsed.");
    }
    return parsedTaskPlan;
  } catch (e) {
    logger.error("Failed to parse task plan", {
      taskPlanString,
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
    return null;
  }
}

function extractProposedPlanFromIssueContent(content: string): string[] | null {
  if (
    !content.includes(PROPOSED_PLAN_OPEN_TAG) ||
    !content.includes(PROPOSED_PLAN_CLOSE_TAG)
  ) {
    return null;
  }
  const proposedPlanString = content
    .split(PROPOSED_PLAN_OPEN_TAG)?.[1]
    ?.split(PROPOSED_PLAN_CLOSE_TAG)?.[0];
  try {
    const parsedProposedPlan = JSON.parse(proposedPlanString.trim());
    return parsedProposedPlan;
  } catch (e) {
    logger.error("Failed to parse proposed plan", {
      proposedPlanString,
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
    return null;
  }
}

type GetIssueTaskPlanInput = {
  githubIssueId: number;
  targetRepository: TargetRepository;
};

export async function getPlansFromIssue(
  input: GetIssueTaskPlanInput,
  config: GraphConfig,
): Promise<{
  taskPlan: TaskPlan | null;
  proposedPlan: string[] | null;
}> {
  if (isLocalMode(config)) {
    return {
      taskPlan: null,
      proposedPlan: null,
    };
  }
  const issue = await getIssue({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken:
      getGitHubTokensFromConfig(config).githubInstallationToken,
  });
  if (!issue || !issue.body) {
    throw new Error(
      "No issue found when attempting to get task plan from issue",
    );
  }

  const taskPlan = extractTasksFromIssueContent(issue.body);
  const proposedPlan = extractProposedPlanFromIssueContent(issue.body);
  return {
    taskPlan,
    proposedPlan,
  };
}

function insertPlanToIssueBody(
  issueBody: string,
  planString: string,
  planType: "taskPlan" | "proposedPlan",
) {
  const openingPlanTag =
    planType === "taskPlan" ? TASK_OPEN_TAG : PROPOSED_PLAN_OPEN_TAG;
  const closingPlanTag =
    planType === "taskPlan" ? TASK_CLOSE_TAG : PROPOSED_PLAN_CLOSE_TAG;

  const wrappedPlan = `${openingPlanTag}
${planString}
${closingPlanTag}`;

  if (
    !issueBody.includes(openingPlanTag) &&
    !issueBody.includes(closingPlanTag)
  ) {
    if (
      !issueBody.includes(DETAILS_OPEN_TAG) &&
      !issueBody.includes(DETAILS_CLOSE_TAG)
    ) {
      return `${issueBody}
${DETAILS_OPEN_TAG}
${AGENT_CONTEXT_DETAILS_SUMMARY}
${wrappedPlan}
${DETAILS_CLOSE_TAG}`;
    } else {
      // No plan present yet, but details already exists.
      const contentBeforeDetailsTag = issueBody.split(DETAILS_OPEN_TAG)?.[0];
      const contentAfterDetailsOpenTag =
        issueBody.split(DETAILS_OPEN_TAG)?.[1] || "";
      const contentAfterSummary = contentAfterDetailsOpenTag.includes(
        AGENT_CONTEXT_DETAILS_SUMMARY,
      )
        ? contentAfterDetailsOpenTag.split(AGENT_CONTEXT_DETAILS_SUMMARY)[1]
        : contentAfterDetailsOpenTag;
      const contentAfterDetailsCloseTag =
        issueBody.split(DETAILS_CLOSE_TAG)?.[1] || "";

      return `${contentBeforeDetailsTag}${DETAILS_OPEN_TAG}
${AGENT_CONTEXT_DETAILS_SUMMARY}
${wrappedPlan}${
        contentAfterSummary.trim()
          ? `
${contentAfterSummary.trim()}`
          : ""
      }
${DETAILS_CLOSE_TAG}${contentAfterDetailsCloseTag}`;
    }
  } else {
    const contentBeforeOpenTag = issueBody.split(openingPlanTag)?.[0];
    const contentAfterCloseTag = issueBody.split(closingPlanTag)?.[1];

    return `${contentBeforeOpenTag}
${wrappedPlan}
${contentAfterCloseTag}`;
  }
}

export async function addProposedPlanToIssue(
  input: GetIssueTaskPlanInput,
  config: GraphConfig,
  proposedPlan: string[],
) {
  const issue = await getIssue({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken:
      getGitHubTokensFromConfig(config).githubInstallationToken,
  });
  if (!issue || !issue.body) {
    throw new Error(
      "No issue found when attempting to get task plan from issue",
    );
  }

  const proposedPlanString = JSON.stringify(proposedPlan, null, 2);
  const newBody = insertPlanToIssueBody(
    issue.body,
    proposedPlanString,
    "proposedPlan",
  );

  await updateIssue({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken:
      getGitHubTokensFromConfig(config).githubInstallationToken,
    body: newBody,
  });
}

export async function addTaskPlanToIssue(
  input: GetIssueTaskPlanInput,
  config: GraphConfig,
  taskPlan: TaskPlan,
): Promise<void> {
  const issue = await getIssue({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken:
      getGitHubTokensFromConfig(config).githubInstallationToken,
  });

  if (!issue || !issue.body) {
    throw new Error("No issue found when attempting to add task plan to issue");
  }

  const taskPlanString = JSON.stringify(taskPlan, null, 2);
  const newBody = insertPlanToIssueBody(issue.body, taskPlanString, "taskPlan");

  await updateIssue({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken:
      getGitHubTokensFromConfig(config).githubInstallationToken,
    body: newBody,
  });
}
