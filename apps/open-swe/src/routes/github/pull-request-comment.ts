import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import { extractLinkedIssues, mentionsOpenSWE } from "./utils.js";

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

    // Get all comments on the PR (issue comments)
    const { data: issueComments } = await octokit.request(
      "GET /repos/{owner}/{repo}/issues/{issue_number}/comments",
      {
        owner,
        repo,
        issue_number: prNumber,
      },
    );

    // Get all review comments (inline code comments)
    const { data: reviewComments } = await octokit.request(
      "GET /repos/{owner}/{repo}/pulls/{pull_number}/comments",
      {
        owner,
        repo,
        pull_number: prNumber,
      },
    );

    // Get all reviews
    const { data: reviews } = await octokit.request(
      "GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews",
      {
        owner,
        repo,
        pull_number: prNumber,
      },
    );

    // Extract linked issues from PR body
    const linkedIssues = extractLinkedIssues(pullRequest.body || "");

    // Create the data object
    const prData = {
      pullRequest: {
        number: prNumber,
        title: pullRequest.title,
        body: pullRequest.body,
        state: pullRequest.state,
        author: pullRequest.user.login,
        created_at: pullRequest.created_at,
        updated_at: pullRequest.updated_at,
        head: {
          ref: pullRequest.head.ref,
          sha: pullRequest.head.sha,
        },
        base: {
          ref: pullRequest.base.ref,
          sha: pullRequest.base.sha,
        },
      },
      triggerComment: {
        id: payload.comment.id,
        body: commentBody,
        author: payload.comment.user?.login,
        created_at: payload.comment.created_at,
      },
      issueComments: issueComments.map((comment) => ({
        id: comment.id,
        body: comment.body,
        author: comment.user?.login,
        created_at: comment.created_at,
        updated_at: comment.updated_at,
      })),
      reviewComments: reviewComments.map((comment) => ({
        id: comment.id,
        body: comment.body,
        author: comment.user?.login,
        path: comment.path,
        line: comment.line,
        diff_hunk: comment.diff_hunk,
        created_at: comment.created_at,
        updated_at: comment.updated_at,
      })),
      reviews: reviews.map((review) => ({
        id: review.id,
        body: review.body,
        author: review.user?.login,
        state: review.state,
        submitted_at: review.submitted_at,
      })),
      linkedIssues: linkedIssues,
      repository: {
        owner,
        name: repo,
        full_name: payload.repository.full_name,
      },
    };

    logger.info("Successfully processed PR comment with @open-swe mention", {
      prNumber,
      commentCount: issueComments.length,
      reviewCommentCount: reviewComments.length,
      reviewCount: reviews.length,
      linkedIssuesCount: linkedIssues.length,
    });

    return prData;
  } catch (error) {
    logger.error("Error processing PR comment webhook:", error);
  }
}
