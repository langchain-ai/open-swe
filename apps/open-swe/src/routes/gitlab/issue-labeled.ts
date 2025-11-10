import { GitLabWebhookHandlerBase } from "./webhook-handler-base.js";
import {
  getOpenSWEAutoAcceptLabel,
  getOpenSWELabel,
  getOpenSWEMaxLabel,
  getOpenSWEMaxAutoAcceptLabel,
} from "../../utils/github/label.js";
import { RequestSource } from "../../constants.js";
import { GraphConfig } from "@openswe/shared/open-swe/types";

class GitLabIssueWebhookHandler extends GitLabWebhookHandlerBase {
  constructor() {
    super("GitLabIssueHandler");
  }

  async handleIssueLabeled(payload: Record<string, any>) {
    if (!process.env.SECRETS_ENCRYPTION_KEY) {
      throw new Error(
        "SECRETS_ENCRYPTION_KEY environment variable is required",
      );
    }

    // GitLab sends label changes in the changes object
    const labelChanges = payload.changes?.labels;
    if (!labelChanges) {
      return;
    }

    // Get current labels from the issue
    const currentLabels = payload.object_attributes?.labels || [];
    const labelNames = currentLabels.map((l: any) => l.title || l);

    const validOpenSWELabels = [
      getOpenSWELabel(),
      getOpenSWEAutoAcceptLabel(),
      getOpenSWEMaxLabel(),
      getOpenSWEMaxAutoAcceptLabel(),
    ];

    // Check if any valid open-swe label is present
    const triggeredLabel = labelNames.find((name: string) =>
      validOpenSWELabels.includes(name as any)
    );

    if (!triggeredLabel) {
      return;
    }

    const isAutoAcceptLabel =
      triggeredLabel === getOpenSWEAutoAcceptLabel() ||
      triggeredLabel === getOpenSWEMaxAutoAcceptLabel();

    const isMaxLabel =
      triggeredLabel === getOpenSWEMaxLabel() ||
      triggeredLabel === getOpenSWEMaxAutoAcceptLabel();

    this.logger.info(
      `'${triggeredLabel}' label added to issue #${payload.object_attributes?.iid}`,
      {
        isAutoAcceptLabel,
        isMaxLabel,
      },
    );

    try {
      const context = await this.setupWebhookContext(payload);
      if (!context) {
        return;
      }

      const issueData = {
        issueNumber: payload.object_attributes?.iid,
        issueTitle: payload.object_attributes?.title,
        issueBody: payload.object_attributes?.description || "",
      };

      const [owner, repo] = context.projectPath.split("/");

      const runInput = {
        messages: [
          this.createHumanMessage(
            `**${issueData.issueTitle}**\n\n${issueData.issueBody}`,
            RequestSource.GITHUB_ISSUE_WEBHOOK, // We can use same constant or create GitLab-specific one
            {
              isOriginalIssue: true,
              githubIssueId: issueData.issueNumber, // Keep field name for compatibility
            },
          ),
        ],
        githubIssueId: issueData.issueNumber, // Keep field name for compatibility
        targetRepository: {
          owner,
          repo,
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

      const { runId, threadId } = await this.createRun(context, {
        runInput,
        configurable,
      });

      await this.createComment(
        context,
        {
          issueOrMrIid: issueData.issueNumber,
          message:
            "🤖 Open SWE has been triggered for this issue. Processing...",
        },
        runId,
        threadId,
        true, // isIssue
      );
    } catch (error) {
      this.handleError(error, "GitLab issue webhook");
    }
  }
}

const issueHandler = new GitLabIssueWebhookHandler();

export async function handleIssueLabeled(payload: Record<string, any>) {
  return issueHandler.handleIssueLabeled(payload);
}
