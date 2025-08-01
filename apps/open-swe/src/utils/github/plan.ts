import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { getGitHubTokensFromConfig } from "../github-tokens.js";
import {
  createIssueComment,
  getIssueComments,
  updateIssueComment,
} from "./api.js";
import { createLogger, LogLevel } from "../logger.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";

const logger = createLogger(LogLevel.INFO, "GitHubPlan");

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

export function cleanTaskItems(taskItem: string): string {
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

  if (isLocalMode(config)) {
    // In local mode, we don't post GitHub comments
    logger.info("Skipping GitHub comment posting in local mode");
    return;
  }

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
