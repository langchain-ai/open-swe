import { WebhookHandlerBase } from "./webhook-handler-base.js";
import {
  getOpenSWEAutoAcceptLabel,
  getOpenSWELabel,
  getOpenSWEMaxLabel,
  getOpenSWEMaxAutoAcceptLabel,
} from "../../utils/github/label.js";
import { RequestSource } from "../../constants.js";
import { GraphConfig } from "@openswe/shared/open-swe/types";

class IssueWebhookHandler extends WebhookHandlerBase {
  constructor() {
    super("GitHubIssueHandler");
  }

  async handleIssueLabeled(payload: any) {
    if (!process.env.SECRETS_ENCRYPTION_KEY) {
      throw new Error(
        "SECRETS_ENCRYPTION_KEY environment variable is required",
      );
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

    this.logger.info(
      `'${payload.label.name}' label added to issue #${payload.issue.number}`,
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
        issueNumber: payload.issue.number,
        issueTitle: payload.issue.title,
        issueBody: payload.issue.body || "",
      };

      const runInput = {
        messages: [
          this.createHumanMessage(
            `**${issueData.issueTitle}**\n\n${issueData.issueBody}`,
            RequestSource.GITHUB_ISSUE_WEBHOOK,
            {
              isOriginalIssue: true,
              githubIssueId: issueData.issueNumber,
            },
          ),
        ],
        githubIssueId: issueData.issueNumber,
        targetRepository: {
          owner: context.owner,
          repo: context.repo,
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
          issueNumber: issueData.issueNumber,
          message:
            "🤖 Open SWE has been triggered for this issue. Processing...",
        },
        runId,
        threadId,
      );
    } catch (error) {
      this.handleError(error, "issue webhook");
    }
  }
}

const issueHandler = new IssueWebhookHandler();

export async function handleIssueLabeled(payload: any) {
  return issueHandler.handleIssueLabeled(payload);
}
