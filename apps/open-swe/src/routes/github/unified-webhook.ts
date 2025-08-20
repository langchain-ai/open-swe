import { Context } from "hono";
import { BlankEnv, BlankInput } from "hono/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { Webhooks } from "@octokit/webhooks";
import { handleIssueLabeled } from "./issue-labeled.js";
import { handlePullRequestComment } from "./pull-request-comment.js";
import { handlePullRequestReview } from "./pull-request-review.js";
import { handlePullRequestReviewComment } from "./pull-request-review-comment.js";

const logger = createLogger(LogLevel.INFO, "GitHubUnifiedWebhook");

const GITHUB_WEBHOOK_SECRET = process.env.GITHUB_WEBHOOK_SECRET!;

const webhooks = new Webhooks({
  secret: GITHUB_WEBHOOK_SECRET,
});

const getPayload = (body: string): Record<string, any> | null => {
  try {
    const payload = JSON.parse(body);
    return payload;
  } catch {
    return null;
  }
};

const getHeaders = (
  c: Context,
): {
  id: string;
  name: string;
  installationId: string;
  targetType: string;
} | null => {
  const headers = c.req.header();
  const webhookId = headers["x-github-delivery"] || "";
  const webhookEvent = headers["x-github-event"] || "";
  const installationId = headers["x-github-hook-installation-target-id"] || "";
  const targetType = headers["x-github-hook-installation-target-type"] || "";
  if (!webhookId || !webhookEvent || !installationId || !targetType) {
    return null;
  }
  return { id: webhookId, name: webhookEvent, installationId, targetType };
};

// Issue labeling events
webhooks.on("issues.labeled", async ({ payload }) => {
  await handleIssueLabeled(payload);
});

// PR general comment events (discussion area)
webhooks.on("issue_comment.created", async ({ payload }) => {
  await handlePullRequestComment(payload);
});

// PR review events (approve/request changes/comment)
webhooks.on("pull_request_review.submitted", async ({ payload }) => {
  await handlePullRequestReview(payload);
});

// PR review comment events (inline code comments)
webhooks.on("pull_request_review_comment.created", async ({ payload }) => {
  await handlePullRequestReviewComment(payload);
});

export async function unifiedWebhookHandler(
  c: Context<BlankEnv, "/webhooks/github", BlankInput>,
) {
  const payload = getPayload(await c.req.text());
  if (!payload) {
    logger.error("Missing payload");
    return c.json({ error: "Missing payload" }, { status: 400 });
  }

  const eventHeaders = getHeaders(c);
  if (!eventHeaders) {
    logger.error("Missing webhook headers");
    return c.json({ error: "Missing webhook headers" }, { status: 400 });
  }

  try {
    await webhooks.receive({
      id: eventHeaders.id,
      name: eventHeaders.name as any,
      payload,
    });

    return c.json({ received: true });
  } catch (error) {
    logger.error("Webhook error:", error);
    return c.json({ error: "Webhook processing failed" }, { status: 400 });
  }
}
