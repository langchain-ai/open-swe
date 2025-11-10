import { GitLabWebhookHandlerBase } from "./webhook-handler-base.js";
import { RequestSource } from "../../constants.js";

class GitLabMergeRequestReviewHandler extends GitLabWebhookHandlerBase {
  constructor() {
    super("GitLabMergeRequestReviewHandler");
  }

  async handleMergeRequestReview(payload: Record<string, any>) {
    if (!process.env.SECRETS_ENCRYPTION_KEY) {
      throw new Error(
        "SECRETS_ENCRYPTION_KEY environment variable is required",
      );
    }

    const action = payload.object_attributes?.action;
    const mrIid = payload.object_attributes?.iid;

    // Only handle approval/unapproval events
    if (action !== "approved" && action !== "unapproved") {
      return;
    }

    this.logger.info(
      `MR !${mrIid} was ${action} by ${payload.user?.username}`,
      {
        mrIid,
        action,
        reviewer: payload.user?.username,
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

      // Create a prompt based on the approval action
      const prompt =
        action === "approved"
          ? `The merge request !${mrIid} has been approved by ${payload.user?.username}.

**Merge Request**: ${mr.title}
**Description**: ${mr.description || "No description"}

The merge request has received approval and may be ready for merging.`
          : `The merge request !${mrIid} has had its approval removed by ${payload.user?.username}.

**Merge Request**: ${mr.title}
**Description**: ${mr.description || "No description"}

Please review the feedback and make any necessary changes.`;

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
        autoAcceptPlan: true,
      };

      const configurable = {
        shouldCreateIssue: false,
        reviewPullNumber: mrIid,
      };

      const { runId, threadId } = await this.createRun(context, {
        runInput,
        configurable,
      });

      const commentMessage =
        action === "approved"
          ? "🎉 Merge request has been approved!"
          : "⚠️ Approval has been removed. Open SWE is reviewing the feedback...";

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
      this.handleError(error, "GitLab MR review webhook");
    }
  }
}

const mrReviewHandler = new GitLabMergeRequestReviewHandler();

export async function handleMergeRequestReview(payload: Record<string, any>) {
  return mrReviewHandler.handleMergeRequestReview(payload);
}
