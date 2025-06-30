import { Hono } from "hono";
import { issueWebhookHandler } from "./github/issue-webhook.js";

export const app = new Hono();

app.post("/webhooks/github", issueWebhookHandler);
