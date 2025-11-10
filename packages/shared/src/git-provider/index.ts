/**
 * Git Provider Module
 *
 * This module exports the git provider abstraction layer,
 * allowing applications to work with multiple git hosting platforms
 * (GitHub, GitLab, etc.) through a unified interface.
 */

// Export types
export type {
  ProviderType,
  User,
  Repository,
  Branch,
  Issue,
  Label,
  Comment,
  PullRequest,
  ReviewComment,
  Review,
  CreatePullRequestParams,
  UpdatePullRequestParams,
  MarkPullRequestReadyParams,
  CreateIssueParams,
  UpdateIssueParams,
  CreateCommentParams,
  UpdateCommentParams,
  CreateReviewCommentReplyParams,
  ListCommentsParams,
  ProviderConfig,
  InstallationInfo,
  WebhookEventType,
  WebhookPayload,
  GitProvider,
  GitProviderFactory,
} from "./types.js";

// Export factory functions
export {
  createGitProvider,
  createGitProviderSimple,
  validateProviderConfig,
} from "./factory.js";

// Export provider implementations
export { GitHubProvider } from "./github/github-provider.js";
export { GitLabProvider } from "./gitlab/gitlab-provider.js";
