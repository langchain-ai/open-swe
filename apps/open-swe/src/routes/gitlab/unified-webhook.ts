import { Context } from "hono";
import { BlankEnv, BlankInput } from "hono/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { handleIssueLabeled } from "./issue-labeled.js";
import { handleMergeRequestComment } from "./merge-request-comment.js";
import { handleMergeRequestReview } from "./merge-request-review.js";

const logger = createLogger(LogLevel.INFO, "GitLabUnifiedWebhook");

const GITLAB_WEBHOOK_TOKEN = process.env.GITLAB_WEBHOOK_TOKEN!;

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
  token: string;
  event: string;
} | null => {
  const headers = c.req.header();
  const token = headers["x-gitlab-token"] || "";
  const event = headers["x-gitlab-event"] || "";

  if (!token || !event) {
    return null;
  }

  return { token, event };
};

const verifyToken = (token: string): boolean => {
  return token === GITLAB_WEBHOOK_TOKEN;
};

const handleWebhook = async (event: string, payload: Record<string, any>) => {
  const objectKind = payload.object_kind;
  const action = payload.object_attributes?.action;

  logger.info("Processing GitLab webhook", { event, objectKind, action });

  // Issue events
  if (objectKind === "issue") {
    if (action === "update" && payload.changes?.labels) {
      // Issue was labeled
      await handleIssueLabeled(payload);
    }
  }

  // Merge request note (comment) events
  if (objectKind === "note") {
    const noteableType = payload.object_attributes?.noteable_type;

    if (noteableType === "MergeRequest") {
      // Comment on merge request
      await handleMergeRequestComment(payload);
    }
  }

  // Merge request events (for reviews/approvals)
  if (objectKind === "merge_request") {
    if (action === "approved" || action === "unapproved") {
      await handleMergeRequestReview(payload);
    }
  }
};

export async function unifiedGitLabWebhookHandler(
  c: Context<BlankEnv, "/webhooks/gitlab", BlankInput>,
) {
  const rawBody = await c.req.text();
  const payload = getPayload(rawBody);

  if (!payload) {
    logger.error("Missing payload");
    return c.json({ error: "Missing payload" }, { status: 400 });
  }

  const eventHeaders = getHeaders(c);
  if (!eventHeaders) {
    logger.error("Missing webhook headers");
    return c.json({ error: "Missing webhook headers" }, { status: 400 });
  }

  // Verify token
  if (!verifyToken(eventHeaders.token)) {
    logger.error("Invalid webhook token");
    return c.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    await handleWebhook(eventHeaders.event, payload);
    return c.json({ received: true });
  } catch (error) {
    logger.error("Webhook error:", error);
    return c.json({ error: "Webhook processing failed" }, { status: 500 });
  }
}
