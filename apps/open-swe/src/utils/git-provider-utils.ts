/**
 * Git Provider Utilities
 *
 * Provides provider-agnostic wrappers around git provider operations
 * for use in graph nodes and other parts of the application.
 */

import { GraphConfig } from "@openswe/shared/open-swe/types";
import { createGitProvider } from "@openswe/shared/git-provider/factory";
import type { GitProvider, ProviderType } from "@openswe/shared/git-provider/types";
import { GIT_PROVIDER_TYPE, GITLAB_BASE_URL } from "@openswe/shared/constants";
import { getGitHubTokensFromConfig } from "./github-tokens.js";
import { decryptSecret } from "@openswe/shared/crypto";

/**
 * Gets the provider type from config, defaulting to GitHub for backward compatibility
 */
export function getProviderType(config: GraphConfig): ProviderType {
  const providerType = (config.configurable as any)?.[GIT_PROVIDER_TYPE];
  return (providerType as ProviderType) || "github";
}

/**
 * Gets the appropriate token for the provider
 */
function getProviderToken(config: GraphConfig, providerType: ProviderType): string {
  if (providerType === "github") {
    const { githubInstallationToken } = getGitHubTokensFromConfig(config);
    return githubInstallationToken;
  } else {
    // GitLab - need to decrypt the token
    const encryptedGitlabToken = (config.configurable as any)?.["x-gitlab-access-token"];
    console.log("[getProviderToken] Encrypted GitLab token:", encryptedGitlabToken?.substring(0, 20) + "...");

    if (!encryptedGitlabToken) {
      throw new Error("GitLab access token not found in config");
    }

    const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
    if (!encryptionKey) {
      throw new Error("Missing SECRETS_ENCRYPTION_KEY environment variable");
    }

    const decryptedToken = decryptSecret(encryptedGitlabToken, encryptionKey);
    console.log("[getProviderToken] Decrypted token starts with:", decryptedToken.substring(0, 10) + "...");
    return decryptedToken;
  }
}

/**
 * Gets the base URL for the provider (mainly for GitLab self-hosted)
 */
function getProviderBaseUrl(config: GraphConfig, providerType: ProviderType): string | undefined {
  if (providerType === "gitlab") {
    return (config.configurable as any)?.[GITLAB_BASE_URL] || "https://gitlab.com";
  }
  return undefined;
}

/**
 * Creates a git provider instance from the config
 */
export function getGitProviderFromConfig(config: GraphConfig): GitProvider {
  const providerType = getProviderType(config);
  const token = getProviderToken(config, providerType);
  const baseUrl = getProviderBaseUrl(config, providerType);

  return createGitProvider({
    type: providerType,
    token,
    baseUrl,
  });
}

/**
 * Provider-agnostic wrapper for creating a pull/merge request
 */
export interface CreatePRParams {
  owner: string;
  repo: string;
  headBranch: string;
  title: string;
  body: string;
  baseBranch?: string;
  draft?: boolean;
}

export async function createPullRequest(
  params: CreatePRParams,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const pr = await provider.createPullRequest({
    owner: params.owner,
    repo: params.repo,
    title: params.title,
    body: params.body,
    head: params.headBranch,
    base: params.baseBranch || "main",
    draft: params.draft,
  });

  // Return in GitHub-compatible format for backward compatibility
  return {
    number: pr.number,
    html_url: pr.url,
    title: pr.title,
    body: pr.body,
    draft: pr.draft,
    state: pr.state,
    head: {
      ref: pr.headBranch,
      sha: pr.headSha,
    },
    base: {
      ref: pr.baseBranch,
    },
  };
}

/**
 * Provider-agnostic wrapper for updating a pull/merge request
 */
export interface UpdatePRParams {
  owner: string;
  repo: string;
  pullNumber: number;
  title?: string;
  body?: string;
}

export async function updatePullRequest(
  params: UpdatePRParams,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const pr = await provider.updatePullRequest({
    owner: params.owner,
    repo: params.repo,
    pullNumber: params.pullNumber,
    title: params.title,
    body: params.body,
  });

  // Return in GitHub-compatible format
  return {
    number: pr.number,
    html_url: pr.url,
    title: pr.title,
    body: pr.body,
    draft: pr.draft,
    state: pr.state,
  };
}

/**
 * Provider-agnostic wrapper for creating an issue
 */
export interface CreateIssueParams {
  owner: string;
  repo: string;
  title: string;
  body: string;
  labels?: string[];
}

export async function createIssue(
  params: CreateIssueParams,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const issue = await provider.createIssue({
    owner: params.owner,
    repo: params.repo,
    title: params.title,
    body: params.body,
    labels: params.labels,
  });

  // Return in GitHub-compatible format
  return {
    id: issue.id,
    number: issue.number,
    title: issue.title,
    body: issue.body,
    state: issue.state,
    html_url: issue.url,
    user: {
      login: issue.author.login,
      id: issue.author.id,
    },
    labels: issue.labels.map((label: any) => ({
      id: label.id,
      name: label.name,
      color: label.color,
    })),
    created_at: issue.createdAt.toISOString(),
    updated_at: issue.updatedAt.toISOString(),
  };
}

/**
 * Provider-agnostic wrapper for creating an issue comment
 */
export interface CreateCommentParams {
  owner: string;
  repo: string;
  issueNumber: number;
  body: string;
}

export async function createIssueComment(
  params: CreateCommentParams,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const comment = await provider.createIssueComment({
    owner: params.owner,
    repo: params.repo,
    issueNumber: params.issueNumber,
    body: params.body,
  });

  // Return in GitHub-compatible format
  return {
    id: typeof comment.id === 'number' ? comment.id : parseInt(comment.id as string),
    body: comment.body,
    user: {
      login: comment.author.login,
      id: comment.author.id,
    },
    created_at: comment.createdAt.toISOString(),
    updated_at: comment.updatedAt.toISOString(),
    html_url: comment.url,
  };
}

/**
 * Provider-agnostic wrapper for updating an issue
 */
export interface UpdateIssueParams {
  owner: string;
  repo: string;
  issueNumber: number;
  body?: string;
  title?: string;
}

export async function updateIssue(
  params: UpdateIssueParams,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const issue = await provider.updateIssue({
    owner: params.owner,
    repo: params.repo,
    issueNumber: params.issueNumber,
    body: params.body,
    title: params.title,
  });

  // Return in GitHub-compatible format
  return {
    number: issue.number,
    title: issue.title,
    body: issue.body,
    state: issue.state,
    html_url: issue.url,
    user: {
      login: issue.author.login,
      id: issue.author.id,
    },
    labels: issue.labels.map((label: any) => ({
      id: label.id,
      name: label.name,
      color: label.color,
    })),
    created_at: issue.createdAt.toISOString(),
    updated_at: issue.updatedAt.toISOString(),
  };
}

/**
 * Provider-agnostic wrapper for getting an issue
 */
export async function getIssue(
  owner: string,
  repo: string,
  issueNumber: number,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const issue = await provider.getIssue(owner, repo, issueNumber);

  // Return in GitHub-compatible format
  return {
    number: issue.number,
    title: issue.title,
    body: issue.body,
    state: issue.state,
    html_url: issue.url,
    user: {
      login: issue.author.login,
      id: issue.author.id,
    },
    labels: issue.labels.map((label: any) => ({
      id: label.id,
      name: label.name,
      color: label.color,
    })),
    created_at: issue.createdAt.toISOString(),
    updated_at: issue.updatedAt.toISOString(),
  };
}

/**
 * Provider-agnostic wrapper for listing issue comments
 */
export async function listIssueComments(
  owner: string,
  repo: string,
  issueNumber: number,
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);

  const comments = await provider.listIssueComments({
    owner,
    repo,
    issueNumber,
  });

  // Return in GitHub-compatible format for backward compatibility
  return comments.map((comment) => ({
    id: typeof comment.id === 'number' ? comment.id : parseInt(comment.id as string),
    node_id: '',  // Not used, but required by type
    url: comment.url,
    issue_url: comment.url,
    body: comment.body,
    user: {
      login: comment.author.login,
      id: comment.author.id,
      node_id: '',
      avatar_url: '',
      gravatar_id: null,
      url: '',
      html_url: '',
      followers_url: '',
      following_url: '',
      gists_url: '',
      starred_url: '',
      subscriptions_url: '',
      organizations_url: '',
      repos_url: '',
      events_url: '',
      received_events_url: '',
      type: 'User',
      site_admin: false,
    },
    created_at: comment.createdAt.toISOString(),
    updated_at: comment.updatedAt.toISOString(),
    html_url: comment.url,
    author_association: 'NONE',
  } as any));
}

/**
 * Provider-agnostic wrapper for adding labels to an issue
 */
export async function addLabelsToIssue(
  owner: string,
  repo: string,
  issueNumber: number,
  labels: string[],
  config: GraphConfig,
) {
  const provider = getGitProviderFromConfig(config);
  await provider.addLabels(owner, repo, issueNumber, labels);
}
