import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import {
  mentionsOpenSWE,
  extractLinkedIssues,
  getPrContext,
  convertPRPayloadToPullRequestObj,
} from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";

const logger = createLogger(LogLevel.INFO, "GitHubPRReviewCommentHandler");

const githubApp = new GitHubApp();

export async function handlePullRequestReviewComment(
  payload: any,
): Promise<any> {
  const commentBody = payload.comment.body;

  // Check if the review comment mentions @open-swe
  if (!mentionsOpenSWE(commentBody)) {
    logger.info("Review comment does not mention @open-swe, skipping");
    return;
  }

  logger.info(
    `@open-swe mentioned in PR #${payload.pull_request.number} review comment`,
    {
      commentId: payload.comment.id,
      author: payload.comment.user?.login,
      path: payload.comment.path,
      line: payload.comment.line,
    },
  );

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
    const prNumber = payload.pull_request.number;

    const { reviews, prComments, linkedIssues } = await getPrContext(octokit, {
      owner,
      repo,
      prNumber,
      linkedIssueNumbers: extractLinkedIssues(payload.pull_request.body || ""),
    });

    const prData: PullRequestReviewTriggerData = {
      pullRequest: convertPRPayloadToPullRequestObj(
        payload.pull_request,
        prNumber,
      ),
      triggerComment: {
        id: payload.comment.id,
        body: commentBody,
        author: payload.comment.user?.login,
        path: payload.comment.path,
        line: payload.comment.line,
        diff_hunk: payload.comment.diff_hunk,
      },
      prComments,
      reviews,
      linkedIssues,
      repository: {
        owner,
        name: repo,
      },
    };

    logger.info(
      "Successfully processed PR review comment with @open-swe mention",
      {
        prNumber,
        commentPath: payload.comment.path,
        commentLine: payload.comment.line,
        commentCount: prComments.length,
        reviewCommentCount: reviews.reduce(
          (acc: number, r: any) => acc + (r.reviewComments?.length ?? 0),
          0,
        ),
        reviewCount: reviews.length,
        linkedIssuesCount: linkedIssues.length,
      },
    );

    return prData;
  } catch (error) {
    logger.error("Error processing PR review comment webhook:", error);
  }
}
