import {
  GraphConfig,
  TargetRepository,
  TaskPlan,
} from "@open-swe/shared/open-swe/types";
import { getIssue, updateIssue } from "./api.js";
import { getGitHubTokensFromConfig } from "../github-tokens.js";
import { createLogger, LogLevel } from "../logger.js";

const logger = createLogger(LogLevel.INFO, "IssueTaskString");

export const TASK_OPEN_TAG = "<open-swe-do-not-edit-task-plan>";
export const TASK_CLOSE_TAG = "</open-swe-do-not-edit-task-plan>";

export const PROPOSED_PLAN_OPEN_TAG = "<open-swe-do-not-edit-proposed-plan>";
export const PROPOSED_PLAN_CLOSE_TAG = "</open-swe-do-not-edit-proposed-plan>";

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

const DETAILS_OPEN_TAG = "<details>";
const DETAILS_CLOSE_TAG = "</details>";
const AGENT_CONTEXT_DETAILS_SUMMARY = "<summary>Agent Context</summary>";

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
  let newBody = "";

  if (
    !issue.body.includes(TASK_OPEN_TAG) &&
    !issue.body.includes(TASK_CLOSE_TAG)
  ) {
    newBody = `${issue.body}

${DETAILS_OPEN_TAG}
${AGENT_CONTEXT_DETAILS_SUMMARY}

${TASK_OPEN_TAG}
${taskPlanString}
${TASK_CLOSE_TAG}

${DETAILS_CLOSE_TAG}`;
  } else {
    const contentBeforeOpenTag = issue.body.split(TASK_OPEN_TAG)?.[0];
    const contentAfterCloseTag = issue.body.split(TASK_CLOSE_TAG)?.[1];
    const newTaskPlanString = JSON.stringify(taskPlan, null, 2);

    newBody = `${contentBeforeOpenTag}

${TASK_OPEN_TAG}
${newTaskPlanString}
${TASK_CLOSE_TAG}

${contentAfterCloseTag}`;
  }

  await updateIssue({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken:
      getGitHubTokensFromConfig(config).githubInstallationToken,
    body: newBody,
  });
}
