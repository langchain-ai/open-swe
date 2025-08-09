import { Hono } from "hono";
import { unifiedWebhookHandler } from "./github/unified-webhook.js";

export const app = new Hono();

app.post("/webhooks/github", unifiedWebhookHandler);
