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

// Handle label added to issue
webhooks.on("issues.labeled", async ({ payload }) => {
  console.log("issues.labeled");
  console.dir(payload, { depth: null });

  // Check if the added label is 'open-swe'
  if (payload.label?.name !== "open-swe") {
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
  const parsedQueryParams = new URLSearchParams(body);
  const payloadString = parsedQueryParams.get("payload");
  if (!payloadString) {
    return null;
  }
  const payload = JSON.parse(payloadString);
  return payload;
};

const getHeaders = (c: Context): { id: string; name: string } | null => {
  const headers = c.req.header();
  const webhookId = headers["x-github-delivery"] || "";
  const webhookEvent = headers["x-github-event"] || "";
  if (!webhookId || !webhookEvent) {
    return null;
  }
  return { id: webhookId, name: webhookEvent };
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
      payload,
    });

    return c.json({ received: true });
  } catch (error) {
    logger.error("Webhook error:", error);
    return c.json({ error: "Webhook processing failed" }, { status: 400 });
  }
}
