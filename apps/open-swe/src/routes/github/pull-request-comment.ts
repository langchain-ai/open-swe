import {
  PRWebhookHandlerBase,
  PRWebhookContext,
} from "./pr-webhook-handler-base.js";
import { constructLinkToPRComment } from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";
import { createPromptFromPRCommentTrigger } from "./prompts.js";
import { getRandomWebhookMessage } from "./webhook-messages.js";

class PRCommentWebhookHandler extends PRWebhookHandlerBase {
  constructor() {
    super("GitHubPRCommentHandler");
  }

  protected createPrompt(prData: PullRequestReviewTriggerData): string {
    return createPromptFromPRCommentTrigger(prData);
  }

  protected createCommentMessage(linkToTrigger: string): string {
    return getRandomWebhookMessage("pr_comment", linkToTrigger);
  }

  protected createTriggerLink(
    context: PRWebhookContext,
    triggerId: number | string,
  ): string {
    return constructLinkToPRComment({
      owner: context.owner,
      repo: context.repo,
      pullNumber: context.prNumber,
      commentId: triggerId as number,
    });
  }

  async handlePullRequestComment(payload: any): Promise<void> {
    // Only process comments on pull requests
    if (!payload.issue.pull_request) {
      return;
    }

    const commentBody = payload.comment.body;

    if (!this.validateOpenSWEMention(commentBody, "Comment")) {
      return;
    }

    this.logger.info(
      `@open-swe mentioned in PR #${payload.issue.number} comment`,
      {
        commentId: payload.comment.id,
        author: payload.comment.user?.login,
      },
    );

    try {
      const context = await this.setupPRWebhookContext(payload);
      if (!context) {
        return;
      }

      // Get full PR details
      const { data: pullRequest } = await context.octokit.request(
        "GET /repos/{owner}/{repo}/pulls/{pull_number}",
        {
          owner: context.owner,
          repo: context.repo,
          pull_number: context.prNumber,
        },
      );

      const { reviews, prComments, linkedIssues } = await this.fetchPRContext(
        context,
        pullRequest.body || "",
      );

      const prData = this.createPRTriggerData(
        pullRequest,
        context.prNumber,
        {
          id: payload.comment.id,
          body: commentBody,
          author: payload.comment.user?.login,
        },
        prComments,
        reviews,
        linkedIssues,
        {
          owner: context.owner,
          name: context.repo,
        },
      );

      const prompt = this.createPrompt(prData);
      const runInput = this.createPRRunInput(prompt, context, pullRequest);
      const configurable = this.createPRRunConfiguration(context);

      const { runId, threadId } = await this.createRun(context, {
        runInput,
        configurable,
      });

      const triggerLink = this.createTriggerLink(context, payload.comment.id);
      const commentMessage = this.createCommentMessage(triggerLink);

      await this.createComment(
        context,
        {
          issueNumber: context.prNumber,
          message: commentMessage,
        },
        runId,
        threadId,
      );
    } catch (error) {
      this.handleError(error, "PR comment webhook");
    }
  }
}

const prCommentHandler = new PRCommentWebhookHandler();

export async function handlePullRequestComment(payload: any): Promise<void> {
  return prCommentHandler.handlePullRequestComment(payload);
}
