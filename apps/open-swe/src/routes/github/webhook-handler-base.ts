import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { GitHubApp } from "../../utils/github-app.js";
import { isAllowedUser } from "@openswe/shared/github/allowed-users";
import { HumanMessage } from "@langchain/core/messages";
import { ManagerGraphUpdate } from "@openswe/shared/open-swe/manager/types";
import { getOpenSweAppUrl } from "../../utils/url-helpers.js";
import { createRunFromWebhook, createDevMetadataComment } from "./utils.js";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { Octokit } from "@octokit/core";
import { isLocalModeFromEnv } from "@openswe/shared/open-swe/local-mode";

export interface WebhookHandlerContext {
  installationId: number;
  octokit: Octokit;
  token: string;
  owner: string;
  repo: string;
  userLogin: string;
  userId: number;
}

export interface RunArgs {
  runInput: ManagerGraphUpdate;
  configurable?: Partial<GraphConfig["configurable"]>;
}

export interface CommentConfiguration {
  issueNumber: number;
  message: string;
}

export class WebhookHandlerBase {
  protected logger: ReturnType<typeof createLogger>;
  protected githubApp?: GitHubApp;

  constructor(loggerName: string) {
    this.logger = createLogger(LogLevel.INFO, loggerName);
    if (isLocalModeFromEnv()) {
      return;
    }
    this.githubApp = new GitHubApp();
  }

  /**
   * Validates and sets up the webhook context with installation and user validation
   */
  protected async setupWebhookContext(
    payload: any,
  ): Promise<WebhookHandlerContext | null> {
    const installationId = payload.installation?.id;
    if (!installationId) {
      this.logger.error("No installation ID found in webhook payload");
      return null;
    }

    if (!isAllowedUser(payload.sender.login)) {
      this.logger.error("User is not a member of allowed orgs", {
        username: payload.sender.login,
      });
      return null;
    }

    if (!this.githubApp) {
      this.logger.error("GitHub integration is disabled");
      return null;
    }

    const [octokit, { token }] = await Promise.all([
      this.githubApp.getInstallationOctokit(installationId),
      this.githubApp.getInstallationAccessToken(installationId),
    ]);

    return {
      installationId,
      octokit,
      token,
      owner: payload.repository.owner.login,
      repo: payload.repository.name,
      userLogin: payload.sender.login,
      userId: payload.sender.id,
    };
  }

  /**
   * Creates a run from webhook with the provided configuration
   */
  protected async createRun(
    context: WebhookHandlerContext,
    args: RunArgs,
  ): Promise<{ runId: string; threadId: string }> {
    const { runId, threadId } = await createRunFromWebhook({
      installationId: context.installationId,
      installationToken: context.token,
      userId: context.userId,
      userLogin: context.userLogin,
      installationName: context.owner,
      runInput: args.runInput,
      configurable: args.configurable || {},
    });

    this.logger.info("Created new run from GitHub webhook.", {
      threadId,
      runId,
    });

    return { runId, threadId };
  }

  /**
   * Creates a comment on the issue/PR with the provided configuration
   */
  protected async createComment(
    context: WebhookHandlerContext,
    config: CommentConfiguration,
    runId: string,
    threadId: string,
  ): Promise<void> {
    this.logger.info("Creating comment...");

    const appUrl = getOpenSweAppUrl(threadId);
    const appUrlCommentText = appUrl
      ? `View run in Open SWE [here](${appUrl}) (this URL will only work for @${context.userLogin})`
      : "";

    const fullMessage = `${config.message}\n\n${appUrlCommentText}\n\n${createDevMetadataComment(runId, threadId)}`;

    await context.octokit.request(
      "POST /repos/{owner}/{repo}/issues/{issue_number}/comments",
      {
        owner: context.owner,
        repo: context.repo,
        issue_number: config.issueNumber,
        body: fullMessage,
      },
    );
  }

  /**
   * Creates a HumanMessage with the provided content and request source
   */
  protected createHumanMessage(
    content: string,
    requestSource: string,
    additionalKwargs: Record<string, any> = {},
  ): HumanMessage {
    return new HumanMessage({
      id: uuidv4(),
      content,
      additional_kwargs: {
        requestSource,
        ...additionalKwargs,
      },
    });
  }

  /**
   * Handles errors consistently across all webhook handlers
   */
  protected handleError(error: any, context: string): void {
    this.logger.error(`Error processing ${context}:`, error);
  }
}
