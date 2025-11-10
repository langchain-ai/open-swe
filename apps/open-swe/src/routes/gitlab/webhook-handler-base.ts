import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { HumanMessage } from "@langchain/core/messages";
import { ManagerGraphUpdate } from "@openswe/shared/open-swe/manager/types";
import { RequestSource } from "../../constants.js";
import { getOpenSweAppUrl } from "../../utils/url-helpers.js";
import { createRunFromWebhook, createDevMetadataComment } from "../github/utils.js";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { Gitlab } from "@gitbeaker/rest";
import { isAllowedUser } from "@openswe/shared/github/allowed-users";

export interface GitLabWebhookHandlerContext {
  gitlabClient: InstanceType<typeof Gitlab>;
  token: string;
  projectId: number;
  projectPath: string;
  userLogin: string;
  userId: number;
  baseUrl: string;
}

export interface RunArgs {
  runInput: ManagerGraphUpdate;
  configurable?: Partial<GraphConfig["configurable"]>;
}

export interface CommentConfiguration {
  issueOrMrIid: number;
  message: string;
}

export class GitLabWebhookHandlerBase {
  protected logger: ReturnType<typeof createLogger>;

  constructor(loggerName: string) {
    this.logger = createLogger(LogLevel.INFO, loggerName);
  }

  /**
   * Validates and sets up the webhook context
   */
  protected async setupWebhookContext(
    payload: any,
  ): Promise<GitLabWebhookHandlerContext | null> {
    const projectId = payload.project?.id;
    const projectPath = payload.project?.path_with_namespace;

    if (!projectId || !projectPath) {
      this.logger.error("No project ID or path found in webhook payload");
      return null;
    }

    const userLogin = payload.user?.username;
    const userId = payload.user?.id;

    if (!userLogin || !userId) {
      this.logger.error("No user information found in webhook payload");
      return null;
    }

    // Check if user is allowed (using same allow list for now)
    if (!isAllowedUser(userLogin)) {
      this.logger.error("User is not in allowed list", {
        username: userLogin,
      });
      return null;
    }

    // Get GitLab token from environment
    const token = process.env.GITLAB_ACCESS_TOKEN || "";
    const baseUrl = process.env.GITLAB_BASE_URL || "https://gitlab.com";

    if (!token) {
      this.logger.error("GITLAB_ACCESS_TOKEN not configured");
      return null;
    }

    const gitlabClient = new Gitlab({
      token,
      host: baseUrl,
    });

    return {
      gitlabClient,
      token,
      projectId,
      projectPath,
      userLogin,
      userId,
      baseUrl,
    };
  }

  /**
   * Creates a run from webhook with the provided configuration
   */
  protected async createRun(
    context: GitLabWebhookHandlerContext,
    args: RunArgs,
  ): Promise<{ runId: string; threadId: string }> {
    const { runId, threadId } = await createRunFromWebhook({
      installationId: context.projectId, // Use project ID as installation ID equivalent
      installationToken: context.token,
      userId: context.userId,
      userLogin: context.userLogin,
      installationName: context.projectPath,
      runInput: args.runInput,
      configurable: {
        ...args.configurable,
        providerType: "gitlab",
        gitlabBaseUrl: context.baseUrl,
      } as any,
    });

    this.logger.info("Created new run from GitLab webhook.", {
      threadId,
      runId,
    });

    return { runId, threadId };
  }

  /**
   * Creates a comment on the issue/MR with the provided configuration
   */
  protected async createComment(
    context: GitLabWebhookHandlerContext,
    config: CommentConfiguration,
    runId: string,
    threadId: string,
    isIssue: boolean = true,
  ): Promise<void> {
    this.logger.info("Creating GitLab comment...");

    const appUrl = getOpenSweAppUrl(threadId);
    const appUrlCommentText = appUrl
      ? `View run in Open SWE [here](${appUrl}) (this URL will only work for @${context.userLogin})`
      : "";

    const fullMessage = `${config.message}\n\n${appUrlCommentText}\n\n${createDevMetadataComment(runId, threadId)}`;

    try {
      if (isIssue) {
        await context.gitlabClient.IssueNotes.create(
          context.projectId,
          config.issueOrMrIid,
          fullMessage,
        );
      } else {
        await context.gitlabClient.MergeRequestNotes.create(
          context.projectId,
          config.issueOrMrIid,
          fullMessage,
        );
      }
    } catch (error) {
      this.logger.error("Failed to create GitLab comment", { error });
      throw error;
    }
  }

  /**
   * Creates a HumanMessage with the provided content and request source
   */
  protected createHumanMessage(
    content: string,
    requestSource: RequestSource,
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
