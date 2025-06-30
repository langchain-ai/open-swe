import { v4 as uuidv4 } from "uuid";
import { Context } from "hono";
import { BlankEnv, BlankInput } from "hono/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import { Webhooks } from "@octokit/webhooks";
import { createLangGraphClient } from "../../utils/langgraph-client.js";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_USER_ID_HEADER,
  GITHUB_USER_LOGIN_HEADER,
  MANAGER_GRAPH_ID,
} from "@open-swe/shared/constants";
import { encryptGitHubToken } from "@open-swe/shared/crypto";
import { HumanMessage } from "@langchain/core/messages";

const logger = createLogger(LogLevel.INFO, "GitHubIssueWebhook");

const GITHUB_WEBHOOK_SECRET = process.env.GITHUB_WEBHOOK_SECRET!;

const githubApp = new GitHubApp();

const webhooks = new Webhooks({
  secret: GITHUB_WEBHOOK_SECRET,
});

const LABEL_NAME = "open-swe";

const getOpenSweAppUrl = (threadId: string) => {
  if (!process.env.OPEN_SWE_APP_URL) {
    return "";
  }
  try {
    const baseUrl = new URL(process.env.OPEN_SWE_APP_URL);
    baseUrl.pathname = `/chat/${threadId}`;
    return baseUrl.toString();
  } catch {
    return "";
  }
};

// Handle label added to issue
webhooks.on("issues.labeled", async ({ payload }) => {
  if (!process.env.GITHUB_TOKEN_ENCRYPTION_KEY) {
    throw new Error(
      "GITHUB_TOKEN_ENCRYPTION_KEY environment variable is required",
    );
  }
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

    const langGraphClient = createLangGraphClient({
      defaultHeaders: {
        [GITHUB_INSTALLATION_TOKEN_COOKIE]: encryptGitHubToken(
          token,
          process.env.GITHUB_TOKEN_ENCRYPTION_KEY,
        ),
        [GITHUB_USER_ID_HEADER]: issueData.userId.toString(),
        [GITHUB_USER_LOGIN_HEADER]: issueData.userLogin,
      },
    });

    const thread = await langGraphClient.threads.create({
      graphId: MANAGER_GRAPH_ID,
    });
    const run = await langGraphClient.runs.create(
      thread.thread_id,
      MANAGER_GRAPH_ID,
      {
        input: {
          messages: [
            new HumanMessage({
              id: uuidv4(),
              content: `**${issueData.issueTitle}**\n\n${issueData.issueBody}`,
              additional_kwargs: {
                isOriginalIssue: true,
                githubIssueId: issueData.issueNumber,
              },
            }),
          ],
          githubIssueId: issueData.issueNumber,
          targetRepository: {
            owner: issueData.owner,
            repo: issueData.repo,
          },
        },
        config: {
          recursion_limit: 400,
        },
        ifNotExists: "create",
        streamResumable: true,
        streamMode: ["values", "messages", "custom"],
      },
    );

    // Trigger your AI agent workflow
    logger.info("CREATE NEW RUN!!", {
      thread_id: thread.thread_id,
      run_id: run.run_id,
      issue_number: issueData.issueNumber,
      owner: issueData.owner,
      repo: issueData.repo,
      user_id: issueData.userId,
      user_login: issueData.userLogin,
    });

    logger.info("Creating comment...");
    const appUrl = getOpenSweAppUrl(thread.thread_id);
    await octokit.request(
      "POST /repos/{owner}/{repo}/issues/{issue_number}/comments",
      {
        owner: issueData.owner,
        repo: issueData.repo,
        issue_number: issueData.issueNumber,
        body: `🤖 Open SWE has been triggered for this issue. Processing...\n\n${appUrl ? `View run in Open SWE [here](${appUrl})` : ""}`,
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
