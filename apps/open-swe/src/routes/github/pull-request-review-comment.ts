import {
  PRWebhookHandlerBase,
  PRWebhookContext,
} from "./pr-webhook-handler-base.js";
import { constructLinkToPRReviewComment } from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";
import { createPromptFromPRReviewCommentTrigger } from "./prompts.js";
import { getRandomWebhookMessage } from "./webhook-messages.js";
import { GITHUB_TRIGGER_USERNAME } from "./constants.js";

class PRReviewCommentWebhookHandler extends PRWebhookHandlerBase {
  constructor() {
    super("GitHubPRReviewCommentHandler");
  }

  protected createPrompt(prData: PullRequestReviewTriggerData): string {
    return createPromptFromPRReviewCommentTrigger(prData);
  }

  protected createCommentMessage(linkToTrigger: string): string {
    return getRandomWebhookMessage("pr_review_comment", linkToTrigger);
  }

  protected createTriggerLink(
    context: PRWebhookContext,
    triggerId: number | string,
  ): string {
    return constructLinkToPRReviewComment({
      owner: context.owner,
      repo: context.repo,
      pullNumber: context.prNumber,
      commentId: triggerId as number,
    });
  }

  async handlePullRequestReviewComment(payload: any): Promise<void> {
    const commentBody = payload.comment.body;

    if (!this.validateOpenSWEMention(commentBody, "Review comment")) {
      return;
    }

    this.logger.info(
      `${GITHUB_TRIGGER_USERNAME} mentioned in PR #${payload.pull_request.number} review comment`,
      {
        commentId: payload.comment.id,
        author: payload.comment.user?.login,
        path: payload.comment.path,
        line: payload.comment.line,
      },
    );

    try {
      const context = await this.setupPRWebhookContext(payload);
      if (!context) {
        return;
      }

      const { reviews, prComments, linkedIssues } = await this.fetchPRContext(
        context,
        payload.pull_request.body || "",
      );

      const prData = this.createPRTriggerData(
        payload.pull_request,
        context.prNumber,
        {
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
        {
          owner: context.owner,
          name: context.repo,
        },
      );

      const prompt = this.createPrompt(prData);
      const runInput = this.createPRRunInput(
        prompt,
        context,
        payload.pull_request,
      );
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
      this.handleError(error, "PR review comment webhook");
    }
  }
}

const prReviewCommentHandler = new PRReviewCommentWebhookHandler();

export async function handlePullRequestReviewComment(
  payload: any,
): Promise<void> {
  return prReviewCommentHandler.handlePullRequestReviewComment(payload);
}
