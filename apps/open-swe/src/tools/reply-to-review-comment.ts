import { tool } from "@langchain/core/tools";
import {
  createReplyToCommentToolFields,
  createReplyToReviewCommentToolFields,
} from "@open-swe/shared/open-swe/tools";
import { getGitHubTokensFromConfig } from "../utils/github-tokens.js";
import { GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import {
  quoteReplyToPullRequestComment,
  replyToReviewComment,
} from "../utils/github/api.js";
import { getRecentUserRequest } from "../utils/user-request.js";
import { RequestSource } from "../constants.js";

export function shouldIncludeReviewCommentTool(
  state: GraphState,
  config: GraphConfig,
): boolean {
  const userMessage = getRecentUserRequest(state.messages, {
    returnFullMessage: true,
    config,
  });
  const shouldIncludeReviewCommentTool =
    userMessage.additional_kwargs?.requestSource ===
      RequestSource.GITHUB_PULL_REQUEST_WEBHOOK ||
    !!config.configurable?.reviewPullNumber;
  return shouldIncludeReviewCommentTool;
}

export function createReplyToReviewCommentTool(
  state: Pick<GraphState, "targetRepository">,
  config: GraphConfig,
) {
  const replyToReviewCommentTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const { githubInstallationToken } = getGitHubTokensFromConfig(config);
      const { reviewPullNumber } = config.configurable ?? {};

      if (!reviewPullNumber) {
        throw new Error("No pull request number found");
      }

      await replyToReviewComment({
        owner: state.targetRepository.owner,
        repo: state.targetRepository.repo,
        commentId: input.id,
        body: input.comment,
        pullNumber: reviewPullNumber,
        githubInstallationToken,
      });

      return {
        result: "Successfully replied to review comment.",
        status: "success",
      };
    },
    createReplyToReviewCommentToolFields(),
  );

  return replyToReviewCommentTool;
}

export function createReplyToCommentTool(
  state: Pick<GraphState, "targetRepository">,
  config: GraphConfig,
) {
  const replyToReviewCommentTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const { githubInstallationToken } = getGitHubTokensFromConfig(config);
      const { reviewPullNumber } = config.configurable ?? {};

      if (!reviewPullNumber) {
        throw new Error("No pull request number found");
      }

      await quoteReplyToPullRequestComment({
        owner: state.targetRepository.owner,
        repo: state.targetRepository.repo,
        commentId: input.id,
        body: input.comment,
        pullNumber: reviewPullNumber,
        githubInstallationToken,
      });

      return {
        result: "Successfully replied to review comment.",
        status: "success",
      };
    },
    createReplyToCommentToolFields(),
  );

  return replyToReviewCommentTool;
}
