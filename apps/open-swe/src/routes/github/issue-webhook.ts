import { Context } from "hono";
import { BlankEnv, BlankInput } from "hono/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import { Webhooks } from "@octokit/webhooks";

const logger = createLogger(LogLevel.INFO, "GitHubIssueWebhook");

const GITHUB_WEBHOOK_SECRET = process.env.GITHUB_WEBHOOK_SECRET!;

const githubApp = new GitHubApp();

const webhooks = new Webhooks({
  secret: GITHUB_WEBHOOK_SECRET,
});

const LABEL_NAME = "open-swe";

// Handle label added to issue
webhooks.on("issues.labeled", async ({ payload }) => {
  if (payload.label?.name !== LABEL_NAME) {
    return;
  }

  logger.info(`'open-swe' label added to issue #${payload.issue.number}`);

  try {
    // Get installation ID from the webhook payload
    const installationId = payload.installation?.id;

    if (!installationId) {
      logger.error("No installation ID found in webhook payload");
      return;
    }

    // Get authenticated Octokit instance for this installation
    const octokit = await githubApp.getInstallationOctokit(installationId);

    // Get installation access token if you need it separately
    const { token, expiresAt } =
      await githubApp.getInstallationAccessToken(installationId);

    logger.info(`Got installation token, expires at: ${expiresAt}`);

    // Extract relevant information from the payload
    const issueData = {
      owner: payload.repository.owner.login,
      repo: payload.repository.name,
      issueNumber: payload.issue.number,
      issueTitle: payload.issue.title,
      issueBody: payload.issue.body || "",
      issueUrl: payload.issue.html_url,
      installationId,
      userId: payload.sender.id,
      userLogin: payload.sender.login,
    };

    // Trigger your AI agent workflow
    logger.info("CREATE NEW RUN!!", {
      ...issueData,
      installationToken: token,
      octokit, // Pass the authenticated Octokit instance
    });

    logger.info("Creating comment...");
    await octokit.request(
      "POST /repos/{owner}/{repo}/issues/{issue_number}/comments",
      {
        owner: issueData.owner,
        repo: issueData.repo,
        issue_number: issueData.issueNumber,
        body: "🤖 Open SWE has been triggered for this issue. Processing...",
      },
    );
  } catch (error) {
    logger.error("Error processing webhook:", error);
  }
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

export async function issueWebhookHandler(
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
    // Verify and process the webhook
    await webhooks.receive({
      id: eventHeaders.id,
      name: eventHeaders.name as any,
      payload: {
        installation: {
          id: Number(eventHeaders.installationId),
          node_id: payload.installation?.node_id,
        },
        ...payload,
      } as any,
    });

    return c.json({ received: true });
  } catch (error) {
    logger.error("Webhook error:", error);
    return c.json({ error: "Webhook processing failed" }, { status: 400 });
  }
}
