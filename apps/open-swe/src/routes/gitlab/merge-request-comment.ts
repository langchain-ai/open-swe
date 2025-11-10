import { GitLabWebhookHandlerBase } from "./webhook-handler-base.js";
import { RequestSource } from "../../constants.js";

const GITLAB_TRIGGER_USERNAME = process.env.GITLAB_TRIGGER_USERNAME || "open-swe";

class GitLabMergeRequestCommentHandler extends GitLabWebhookHandlerBase {
  constructor() {
    super("GitLabMergeRequestCommentHandler");
  }

  /**
   * Checks if the comment mentions the trigger username
   */
  private mentionsTrigger(commentBody: string): boolean {
    const mentionPattern = new RegExp(`@${GITLAB_TRIGGER_USERNAME}\\b`, "i");
    return mentionPattern.test(commentBody);
  }

  async handleMergeRequestComment(payload: Record<string, any>) {
    if (!process.env.SECRETS_ENCRYPTION_KEY) {
      throw new Error(
        "SECRETS_ENCRYPTION_KEY environment variable is required",
      );
    }

    // Only process merge request notes
    if (payload.object_attributes?.noteable_type !== "MergeRequest") {
      return;
    }

    const commentBody = payload.object_attributes?.note || "";

    if (!this.mentionsTrigger(commentBody)) {
      this.logger.info(
        `Comment does not mention @${GITLAB_TRIGGER_USERNAME}, skipping`,
      );
      return;
    }

    const mrIid = payload.merge_request?.iid;
    const commentId = payload.object_attributes?.id;

    this.logger.info(
      `${GITLAB_TRIGGER_USERNAME} mentioned in MR !${mrIid} comment`,
      {
        commentId,
        author: payload.user?.username,
      },
    );

    try {
      const context = await this.setupWebhookContext(payload);
      if (!context) {
        return;
      }

      // Get full MR details
      const mr = await context.gitlabClient.MergeRequests.show(
        context.projectId,
        mrIid,
      );

      const [owner, repo] = context.projectPath.split("/");

      // Create a simple prompt from the comment
      const prompt = `A user has commented on merge request !${mrIid}:

**Merge Request**: ${mr.title}
**Description**: ${mr.description || "No description"}

**User's Comment**: ${commentBody}

Please address the user's request and update the merge request accordingly.`;

      const runInput = {
        messages: [
          this.createHumanMessage(
            prompt,
            RequestSource.GITHUB_PULL_REQUEST_WEBHOOK, // Reuse constant for now
          ),
        ],
        targetRepository: {
          owner,
          repo,
          branch: mr.source_branch as string,
        },
        autoAcceptPlan: true, // Auto-accept for MR comments
      };

      const configurable = {
        shouldCreateIssue: false,
        reviewPullNumber: mrIid, // Keep field name for compatibility
      };

      const { runId, threadId } = await this.createRun(context, {
        runInput,
        configurable,
      });

      const commentMessage = `🤖 Open SWE is processing your request from [this comment](${payload.object_attributes?.url})...`;

      await this.createComment(
        context,
        {
          issueOrMrIid: mrIid,
          message: commentMessage,
        },
        runId,
        threadId,
        false, // isMergeRequest
      );
    } catch (error) {
      this.handleError(error, "GitLab MR comment webhook");
    }
  }
}

const mrCommentHandler = new GitLabMergeRequestCommentHandler();

export async function handleMergeRequestComment(payload: Record<string, any>) {
  return mrCommentHandler.handleMergeRequestComment(payload);
}
