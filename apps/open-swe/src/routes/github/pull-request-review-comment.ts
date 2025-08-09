import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import { mentionsOpenSWE, extractLinkedIssues } from "./utils.js";

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
    const linkedIssues = extractLinkedIssues(payload.pull_request.body || "");

    // Create the data object
    const prData = {
      pullRequest: {
        number: prNumber,
        title: payload.pull_request.title,
        body: payload.pull_request.body,
        state: payload.pull_request.state,
        author: payload.pull_request.user?.login,
        created_at: payload.pull_request.created_at,
        updated_at: payload.pull_request.updated_at,
        head: {
          ref: payload.pull_request.head.ref,
          sha: payload.pull_request.head.sha,
        },
        base: {
          ref: payload.pull_request.base.ref,
          sha: payload.pull_request.base.sha,
        },
      },
      triggerReviewComment: {
        id: payload.comment.id,
        body: commentBody,
        author: payload.comment.user?.login,
        path: payload.comment.path,
        line: payload.comment.line,
        diff_hunk: payload.comment.diff_hunk,
        created_at: payload.comment.created_at,
        updated_at: payload.comment.updated_at,
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

    logger.info(
      "Successfully processed PR review comment with @open-swe mention",
      {
        prNumber,
        commentPath: payload.comment.path,
        commentLine: payload.comment.line,
        commentCount: issueComments.length,
        reviewCommentCount: reviewComments.length,
        reviewCount: reviews.length,
        linkedIssuesCount: linkedIssues.length,
      },
    );

    return prData;
  } catch (error) {
    logger.error("Error processing PR review comment webhook:", error);
  }
}
