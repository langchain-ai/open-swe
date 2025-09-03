import { Hono } from "hono";
import { ENABLE_GITHUB } from "@openswe/shared/config";

export const app = new Hono();

if (ENABLE_GITHUB) {
  const { unifiedWebhookHandler } = await import("./github/unified-webhook.js");
  app.post("/webhooks/github", unifiedWebhookHandler);
}
