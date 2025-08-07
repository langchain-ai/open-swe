import { v4 as uuidv4 } from "uuid";
import { Context } from "hono";
import { BlankEnv, BlankInput } from "hono/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import { Webhooks } from "@octokit/webhooks";
import { createLangGraphClient } from "../../utils/langgraph-client.js";
import {
  GITHUB_INSTALLATION_ID,
  GITHUB_INSTALLATION_NAME,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_USER_ID_HEADER,
  GITHUB_USER_LOGIN_HEADER,
  MANAGER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
} from "@open-swe/shared/constants";
import { encryptSecret } from "@open-swe/shared/crypto";
import { HumanMessage } from "@langchain/core/messages";
import {
  getOpenSWEAutoAcceptLabel,
  getOpenSWELabel,
  getOpenSWEMaxLabel,
  getOpenSWEMaxAutoAcceptLabel,
} from "../../utils/github/label.js";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";
import { RequestSource } from "../../constants.js";
import { isAllowedUser } from "@open-swe/shared/github/allowed-users";
import { getOpenSweAppUrl } from "../../utils/url-helpers.js";
import { StreamMode } from "@langchain/langgraph-sdk";

const logger = createLogger(LogLevel.INFO, "GitHubIssueWebhook");

const GITHUB_WEBHOOK_SECRET = process.env.GITHUB_WEBHOOK_SECRET!;

const githubApp = new GitHubApp();

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

const createDevMetadataComment = (runId: string, threadId: string) => {
  return `<details>
  <summary>Dev Metadata</summary>
  ${JSON.stringify(
    {
      runId,
      threadId,
    },
    null,
    2,
  )}
</details>`;
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

webhooks.on("issues.labeled", async ({ payload }) => {
  if (!process.env.SECRETS_ENCRYPTION_KEY) {
    throw new Error("SECRETS_ENCRYPTION_KEY environment variable is required");
  }
  const validOpenSWELabels = [
    getOpenSWELabel(),
    getOpenSWEAutoAcceptLabel(),
    getOpenSWEMaxLabel(),
    getOpenSWEMaxAutoAcceptLabel(),
  ];
  if (
    !payload.label?.name ||
    !validOpenSWELabels.some((l) => l === payload.label?.name)
  ) {
    return;
  }
  const isAutoAcceptLabel =
    payload.label.name === getOpenSWEAutoAcceptLabel() ||
    payload.label.name === getOpenSWEMaxAutoAcceptLabel();

  const isMaxLabel =
    payload.label.name === getOpenSWEMaxLabel() ||
    payload.label.name === getOpenSWEMaxAutoAcceptLabel();

  logger.info(
    `'${payload.label.name}' label added to issue #${payload.issue.number}`,
    {
      isAutoAcceptLabel,
      isMaxLabel,
    },
  );

  try {
    // Get installation ID from the webhook payload
    const installationId = payload.installation?.id;

    if (!installationId) {
      logger.error("No installation ID found in webhook payload");
      return;
    }

    const [octokit, { token }] = await Promise.all([
      githubApp.getInstallationOctokit(installationId),
      githubApp.getInstallationAccessToken(installationId),
    ]);
    const issueData = {
      owner: payload.repository.owner.login,
      repo: payload.repository.name,
      issueNumber: payload.issue.number,
      issueTitle: payload.issue.title,
      issueBody: payload.issue.body || "",
      userId: payload.sender.id,
      userLogin: payload.sender.login,
    };

    if (!isAllowedUser(issueData.userLogin)) {
      logger.error("User is not a member of allowed orgs", {
        username: issueData.userLogin,
      });
      return;
    }

    const langGraphClient = createLangGraphClient({
      defaultHeaders: {
        [GITHUB_INSTALLATION_TOKEN_COOKIE]: encryptSecret(
          token,
          process.env.SECRETS_ENCRYPTION_KEY,
        ),
        [GITHUB_INSTALLATION_NAME]: issueData.owner,
        [GITHUB_USER_ID_HEADER]: issueData.userId.toString(),
        [GITHUB_USER_LOGIN_HEADER]: issueData.userLogin,
        [GITHUB_INSTALLATION_ID]: installationId.toString(),
      },
    });

    const threadId = uuidv4();
    const runInput: ManagerGraphUpdate = {
      messages: [
        new HumanMessage({
          id: uuidv4(),
          content: `**${issueData.issueTitle}**\n\n${issueData.issueBody}`,
          additional_kwargs: {
            isOriginalIssue: true,
            githubIssueId: issueData.issueNumber,
            requestSource: RequestSource.GITHUB_ISSUE_WEBHOOK,
          },
        }),
      ],
      githubIssueId: issueData.issueNumber,
      targetRepository: {
        owner: issueData.owner,
        repo: issueData.repo,
      },
      autoAcceptPlan: isAutoAcceptLabel,
    };
    // Create config object with Claude Opus 4.1 model configuration for max labels
    const config: Record<string, any> = {
      recursion_limit: 400,
    };

    if (isMaxLabel) {
      config.configurable = {
        plannerModelName: "anthropic:claude-opus-4-1",
        programmerModelName: "anthropic:claude-opus-4-1",
      };
    }

    const run = await langGraphClient.runs.create(threadId, MANAGER_GRAPH_ID, {
      input: runInput,
      config,
      ifNotExists: "create",
      streamResumable: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    });

    logger.info("Created new run from GitHub issue.", {
      threadId,
      runId: run.run_id,
      issueNumber: issueData.issueNumber,
      owner: issueData.owner,
      repo: issueData.repo,
      userId: issueData.userId,
      userLogin: issueData.userLogin,
      autoAcceptPlan: isAutoAcceptLabel,
    });

    logger.info("Creating comment...");
    const appUrl = getOpenSweAppUrl(threadId);
    const appUrlCommentText = appUrl
      ? `View run in Open SWE [here](${appUrl}) (this URL will only work for @${issueData.userLogin})`
      : "";
    await octokit.request(
      "POST /repos/{owner}/{repo}/issues/{issue_number}/comments",
      {
        owner: issueData.owner,
        repo: issueData.repo,
        issue_number: issueData.issueNumber,
        body: `🤖 Open SWE has been triggered for this issue. Processing...\n\n${appUrlCommentText}\n\n${createDevMetadataComment(run.run_id, threadId)}`,
      },
    );
  } catch (error) {
    logger.error("Error processing webhook:", error);
  }
});

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
