import { HTTPException } from "@langchain/langgraph-sdk/auth";
import { Webhooks } from "@octokit/webhooks";
import { createLogger, LogLevel } from "../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "GitHubWebhookAuth");

export async function verifyGitHubWebhookOrThrow(request: Request) {
  const secret = process.env.GITHUB_WEBHOOK_SECRET;
  if (!secret) {
    throw new Error("Missing GITHUB_WEBHOOK_SECRET environment variable.");
  }
  const webhooks = new Webhooks({
    secret,
  });

  const requestClone = request.clone();

  const githubDeliveryHeader = requestClone.headers.get("x-github-delivery");
  const githubEventHeader = requestClone.headers.get("x-github-event");
  const githubSignatureHeader = requestClone.headers.get("x-hub-signature-256");
  const githubInstallationIdHeader = requestClone.headers.get(
    "x-github-hook-installation-target-id",
  );
  if (
    !githubDeliveryHeader ||
    !githubEventHeader ||
    !githubSignatureHeader ||
    !githubInstallationIdHeader
  ) {
    throw new HTTPException(401, {
      message: "Missing GitHub webhook headers.",
    });
  }

  const payload = await requestClone.text();
  const signature = await webhooks.sign(payload);
  const isValid = await webhooks.verify(payload, signature);
  if (!isValid) {
    logger.error("Failed to verify GitHub webhook");
    throw new HTTPException(401, {
      message: "Invalid GitHub webhook signature.",
    });
  }

  return {
    identity: "x-internal-github-bot",
    is_authenticated: true,
    display_name: "GitHub Bot",
    metadata: {
      installation_id: githubInstallationIdHeader,
    },
    permissions: [
      "threads:create",
      "threads:create_run",
      "threads:read",
      "threads:delete",
      "threads:update",
      "threads:search",
      "assistants:create",
      "assistants:read",
      "assistants:delete",
      "assistants:update",
      "assistants:search",
      "deployments:read",
      "deployments:search",
      "store:access",
    ],
  };
}
