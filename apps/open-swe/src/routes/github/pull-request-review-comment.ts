import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import {
  mentionsOpenSWE,
  extractLinkedIssues,
  getPrContext,
  convertPRPayloadToPullRequestObj,
  createRunFromWebhook,
  createDevMetadataComment,
  constructLinkToPRReviewComment,
} from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";
import { createPromptFromPRReviewCommentTrigger } from "./prompts.js";
import { isAllowedUser } from "@open-swe/shared/github/allowed-users";
import { HumanMessage } from "@langchain/core/messages";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";
import { RequestSource } from "../../constants.js";
import { getOpenSweAppUrl } from "../../utils/url-helpers.js";

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

    const prompt = createPromptFromPRReviewCommentTrigger(prData);

    try {
      // Get installation ID from the webhook payload
      const installationId = payload.installation?.id;

      if (!installationId) {
        logger.error("No installation ID found in webhook payload");
        return;
      }

      const [octokit, { token }] = await Promise.all([
        githubApp.getInstallationOctokit(installationId),
        githubApp.getInstallationAccessToken(installationId),
      ]);

      if (!isAllowedUser(payload.sender.login)) {
        logger.error("User is not a member of allowed orgs", {
          username: payload.sender.login,
        });
        return;
      }

      const runInput: ManagerGraphUpdate = {
        messages: [
          new HumanMessage({
            id: uuidv4(),
            content: prompt,
            additional_kwargs: {
              requestSource: RequestSource.GITHUB_PULL_REQUEST_WEBHOOK,
            },
          }),
        ],
        targetRepository: {
          owner: payload.repository.owner.login,
          repo: payload.repository.name,
          branch: payload.pull_request.head.ref,
        },
        autoAcceptPlan: true,
      };

      const { runId, threadId } = await createRunFromWebhook({
        installationId,
        installationToken: token,
        userId: payload.sender.id,
        userLogin: payload.sender.login,
        installationName: payload.repository.owner.login,
        runInput,
        configurable: {
          shouldCreateIssue: false,
          reviewPullNumber: payload.pull_request.number,
        },
      });

      logger.info("Created new run from GitHub review.", {
        threadId,
        runId,
      });

      logger.info("Creating comment...");
      const reviewCommentLink = constructLinkToPRReviewComment({
        owner: payload.repository.owner.login,
        repo: payload.repository.name,
        pullNumber: payload.pull_request.number,
        commentId: payload.comment.id,
      });
      const appUrl = getOpenSweAppUrl(threadId);
      const appUrlCommentText = appUrl
        ? `View run in Open SWE [here](${appUrl}) (this URL will only work for @${payload.sender.login})`
        : "";
      await octokit.request(
        "POST /repos/{owner}/{repo}/issues/{issue_number}/comments",
        {
          owner: payload.repository.owner.login,
          repo: payload.repository.name,
          issue_number: payload.pull_request.number,
          body: `🤖 Open SWE will process [this PR review comment](${reviewCommentLink}). Running...\n\n${appUrlCommentText}\n\n${createDevMetadataComment(runId, threadId)}`,
        },
      );
    } catch (error) {
      logger.error("Error processing PR review comment webhook:", error);
    }

    return prData;
  } catch (error) {
    logger.error("Error processing PR review comment webhook:", error);
  }
}
