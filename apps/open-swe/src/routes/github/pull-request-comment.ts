import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import {
  constructLinkToPRComment,
  convertPRPayloadToPullRequestObj,
  createDevMetadataComment,
  createRunFromWebhook,
  extractLinkedIssues,
  getPrContext,
  mentionsOpenSWE,
} from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";
import { isAllowedUser } from "@open-swe/shared/github/allowed-users";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";
import { HumanMessage } from "@langchain/core/messages";
import { RequestSource } from "../../constants.js";
import { createPromptFromPRTrigger } from "./prompts.js";
import { getOpenSweAppUrl } from "../../utils/url-helpers.js";

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
      linkedIssueNumbers: extractLinkedIssues(pullRequest.body || ""),
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

    const prompt = createPromptFromPRTrigger(prData);

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
          branch: pullRequest.head.ref,
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
          reviewPullNumber: prNumber,
        },
      });

      logger.info("Created new run from GitHub review.", {
        threadId,
        runId,
      });

      logger.info("Creating comment...");
      const commentLink = constructLinkToPRComment({
        owner: payload.repository.owner.login,
        repo: payload.repository.name,
        pullNumber: prNumber,
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
          issue_number: prNumber,
          body: `🤖 Open SWE will process [this PR comment](${commentLink}). Running...\n\n${appUrlCommentText}\n\n${createDevMetadataComment(runId, threadId)}`,
        },
      );
    } catch (error) {
      logger.error("Error starting run from PR comment webhook:", error);
    }

    return prData;
  } catch (error) {
    logger.error("Error processing PR comment webhook:", error);
  }
}
