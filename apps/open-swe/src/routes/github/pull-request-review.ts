import {
  PRWebhookHandlerBase,
  PRWebhookContext,
} from "./pr-webhook-handler-base.js";
import { constructLinkToPRReview } from "./utils.js";
import { PullRequestReviewTriggerData } from "./types.js";
import { createPromptFromPRReviewTrigger } from "./prompts.js";
import { getRandomWebhookMessage } from "./webhook-messages.js";
import { GITHUB_TRIGGER_USERNAME } from "./constants.js";

class PRReviewWebhookHandler extends PRWebhookHandlerBase {
  constructor() {
    super("GitHubPRReviewHandler");
  }

  protected createPrompt(prData: PullRequestReviewTriggerData): string {
    return createPromptFromPRReviewTrigger(prData);
  }

  protected createCommentMessage(linkToTrigger: string): string {
    return getRandomWebhookMessage("pr_review", linkToTrigger);
  }

  protected createTriggerLink(
    context: PRWebhookContext,
    triggerId: number | string,
  ): string {
    return constructLinkToPRReview({
      owner: context.owner,
      repo: context.repo,
      pullNumber: context.prNumber,
      reviewId: triggerId as number,
    });
  }

  async handlePullRequestReview(payload: any): Promise<void> {
    const reviewBody = payload.review.body;

    if (!this.validateOpenSWEMention(reviewBody, "Review")) {
      return;
    }

    this.logger.info(
      `${GITHUB_TRIGGER_USERNAME} mentioned in PR #${payload.pull_request.number} review`,
      {
        reviewId: payload.review.id,
        author: payload.review.user?.login,
        state: payload.review.state,
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
          id: payload.review.id,
          body: reviewBody,
          author: payload.review.user?.login,
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

      const triggerLink = this.createTriggerLink(context, payload.review.id);
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
      this.handleError(error, "PR review webhook");
    }
  }
}

const prReviewHandler = new PRReviewWebhookHandler();

export async function handlePullRequestReview(payload: any): Promise<void> {
  return prReviewHandler.handlePullRequestReview(payload);
}
