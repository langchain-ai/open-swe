import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
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
import { createDevMetadataComment, createRunFromWebhook } from "./utils.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

const logger = createLogger(LogLevel.INFO, "GitHubIssueHandler");

const githubApp = new GitHubApp();

export async function handleIssueLabeled(payload: any) {
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

    const runInput: ManagerGraphUpdate = {
      messages: [
        new HumanMessage({
          id: uuidv4(),
          content: `**${issueData.issueTitle}**\n\n${issueData.issueBody}`,
          additional_kwargs: {
            isOriginalIssue: true,
            githubIssueId: issueData.issueNumber,
            requestSource: RequestSource.GITHUB_WEBHOOK,
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
    const configurable: Partial<GraphConfig["configurable"]> = isMaxLabel
      ? {
          plannerModelName: "anthropic:claude-opus-4-1",
          programmerModelName: "anthropic:claude-opus-4-1",
        }
      : {};

    const { runId, threadId } = await createRunFromWebhook({
      installationId,
      installationToken: token,
      userId: issueData.userId,
      userLogin: issueData.userLogin,
      installationName: issueData.owner,
      runInput,
      configurable,
    });

    logger.info("Created new run from GitHub issue.", {
      threadId,
      runId,
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
        body: `🤖 Open SWE has been triggered for this issue. Processing...\n\n${appUrlCommentText}\n\n${createDevMetadataComment(runId, threadId)}`,
      },
    );
  } catch (error) {
    logger.error("Error processing issue webhook:", error);
  }
}
