import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import {
  convertPRPayloadToPullRequestObj,
  extractLinkedIssues,
  getPrContext,
  mentionsOpenSWE,
} from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";

const logger = createLogger(LogLevel.INFO, "GitHubPRCommentHandler");

const githubApp = new GitHubApp();

export async function handlePullRequestComment(payload: any): Promise<any> {
  // Only process comments on pull requests
  if (!payload.issue.pull_request) {
    return;
  }

  const commentBody = payload.comment.body;

  if (!mentionsOpenSWE(commentBody)) {
    logger.info("Comment does not mention @open-swe, skipping");
    return;
  }

  logger.info(`@open-swe mentioned in PR #${payload.issue.number} comment`, {
    commentId: payload.comment.id,
    author: payload.comment.user?.login,
  });

  try {
    // Get installation ID from the webhook payload
    const installationId = payload.installation?.id;

    if (!installationId) {
      logger.error("No installation ID found in webhook payload");
      return;
    }

    const octokit = await githubApp.getInstallationOctokit(installationId);

    const owner = payload.repository.owner.login;
    const repo = payload.repository.name;
    const prNumber = payload.issue.number;

    // Get full PR details
    const { data: pullRequest } = await octokit.request(
      "GET /repos/{owner}/{repo}/pulls/{pull_number}",
      {
        owner,
        repo,
        pull_number: prNumber,
      },
    );

    const { reviews, prComments, linkedIssues } = await getPrContext(octokit, {
      owner,
      repo,
      prNumber,
      linkedIssueNumbers: extractLinkedIssues(payload.pull_request.body || ""),
    });

    const prData: PullRequestReviewTriggerData = {
      pullRequest: convertPRPayloadToPullRequestObj(pullRequest, prNumber),
      triggerComment: {
        id: payload.comment.id,
        body: commentBody,
        author: payload.comment.user?.login,
      },
      prComments,
      reviews,
      linkedIssues,
      repository: {
        owner,
        name: repo,
      },
    };

    logger.info("Successfully processed PR comment with @open-swe mention", {
      prNumber,
      commentCount: prComments.length,
      reviewCommentCount: reviews.reduce(
        (acc: number, r: any) => acc + (r.reviewComments?.length ?? 0),
        0,
      ),
      reviewCount: reviews.length,
      linkedIssuesCount: linkedIssues.length,
    });

    return prData;
  } catch (error) {
    logger.error("Error processing PR comment webhook:", error);
  }
}
