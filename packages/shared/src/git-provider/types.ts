/**
 * Git Provider Abstraction Layer
 *
 * This module defines provider-agnostic interfaces that can be implemented
 * by different git hosting services (GitHub, GitLab, Bitbucket, etc.)
 */

// ============================================================================
// Provider Type
// ============================================================================

export type ProviderType = 'github' | 'gitlab';

// ============================================================================
// Common Data Types
// ============================================================================

export interface User {
  id: string | number;
  login: string;
  name?: string;
  email?: string;
  avatarUrl?: string;
}

export interface Repository {
  id: string | number;
  owner: string;
  name: string;
  fullName: string; // e.g., "owner/repo"
  defaultBranch: string;
  url: string;
  cloneUrl: string;
  private: boolean;
}

export interface Branch {
  name: string;
  sha: string;
  protected: boolean;
}

export interface Issue {
  id: string | number;
  number: number;
  title: string;
  body: string | null;
  state: 'open' | 'closed';
  author: User;
  url: string;
  labels: Label[];
  createdAt: Date;
  updatedAt: Date;
}

export interface Label {
  id: string | number;
  name: string;
  color: string;
  description?: string;
}

export interface Comment {
  id: string | number;
  body: string;
  author: User;
  createdAt: Date;
  updatedAt: Date;
  url: string;
}

export interface PullRequest {
  id: string | number;
  number: number;
  title: string;
  body: string | null;
  state: 'open' | 'closed' | 'merged';
  draft: boolean;
  url: string;
  headBranch: string;
  baseBranch: string;
  headSha: string;
  author: User;
  createdAt: Date;
  updatedAt: Date;
  mergeable?: boolean;
  labels: Label[];
}

export interface ReviewComment {
  id: string | number;
  body: string;
  author: User;
  path: string;
  line?: number;
  createdAt: Date;
  url: string;
  inReplyTo?: string | number;
}

export interface Review {
  id: string | number;
  author: User;
  state: 'approved' | 'changes_requested' | 'commented' | 'pending';
  body: string | null;
  submittedAt?: Date;
}

// ============================================================================
// Operation Parameters
// ============================================================================

export interface CreatePullRequestParams {
  owner: string;
  repo: string;
  title: string;
  body: string;
  head: string; // source branch
  base: string; // target branch
  draft?: boolean;
}

export interface UpdatePullRequestParams {
  owner: string;
  repo: string;
  pullNumber: number;
  title?: string;
  body?: string;
  state?: 'open' | 'closed';
}

export interface MarkPullRequestReadyParams {
  owner: string;
  repo: string;
  pullNumber: number;
}

export interface CreateIssueParams {
  owner: string;
  repo: string;
  title: string;
  body: string;
  labels?: string[];
}

export interface UpdateIssueParams {
  owner: string;
  repo: string;
  issueNumber: number;
  title?: string;
  body?: string;
  state?: 'open' | 'closed';
  labels?: string[];
}

export interface CreateCommentParams {
  owner: string;
  repo: string;
  issueNumber: number;
  body: string;
}

export interface UpdateCommentParams {
  owner: string;
  repo: string;
  commentId: number;
  body: string;
}

export interface CreateReviewCommentReplyParams {
  owner: string;
  repo: string;
  pullNumber: number;
  body: string;
  inReplyTo: number;
}

export interface ListCommentsParams {
  owner: string;
  repo: string;
  issueNumber: number;
}

// ============================================================================
// Provider Configuration
// ============================================================================

export interface ProviderConfig {
  type: ProviderType;
  token: string;
  baseUrl?: string; // For self-hosted instances (GitLab)
  appId?: string; // For GitHub App
  privateKey?: string; // For GitHub App
  webhookSecret?: string;
}

export interface InstallationInfo {
  id: string | number;
  name: string;
  type: 'user' | 'organization' | 'group';
  avatarUrl?: string;
}

// ============================================================================
// Webhook Types
// ============================================================================

export type WebhookEventType =
  | 'issue.labeled'
  | 'issue.opened'
  | 'issue.comment'
  | 'pull_request.opened'
  | 'pull_request.comment'
  | 'pull_request.review'
  | 'pull_request.review_comment';

export interface WebhookPayload {
  eventType: WebhookEventType;
  provider: ProviderType;
  repository: Repository;
  sender: User;
  issue?: Issue;
  pullRequest?: PullRequest;
  comment?: Comment;
  review?: Review;
  reviewComment?: ReviewComment;
  label?: Label;
  installation?: InstallationInfo;
  rawPayload: any; // Original provider-specific payload
}

// ============================================================================
// Git Provider Interface
// ============================================================================

export interface GitProvider {
  readonly type: ProviderType;

  // ========== Repository Operations ==========
  getRepository(owner: string, repo: string): Promise<Repository>;
  getBranch(owner: string, repo: string, branch: string): Promise<Branch>;

  // ========== Issue Operations ==========
  getIssue(owner: string, repo: string, issueNumber: number): Promise<Issue>;
  createIssue(params: CreateIssueParams): Promise<Issue>;
  updateIssue(params: UpdateIssueParams): Promise<Issue>;
  listIssueComments(params: ListCommentsParams): Promise<Comment[]>;

  // ========== Comment Operations ==========
  createIssueComment(params: CreateCommentParams): Promise<Comment>;
  updateIssueComment(params: UpdateCommentParams): Promise<Comment>;

  // ========== Pull/Merge Request Operations ==========
  getPullRequest(owner: string, repo: string, pullNumber: number): Promise<PullRequest>;
  createPullRequest(params: CreatePullRequestParams): Promise<PullRequest>;
  updatePullRequest(params: UpdatePullRequestParams): Promise<PullRequest>;
  markPullRequestReady(params: MarkPullRequestReadyParams): Promise<PullRequest>;

  // ========== Review Comment Operations ==========
  createReviewCommentReply(params: CreateReviewCommentReplyParams): Promise<Comment>;

  // ========== Label Operations ==========
  addLabels(owner: string, repo: string, issueNumber: number, labels: string[]): Promise<void>;
  removeLabel(owner: string, repo: string, issueNumber: number, label: string): Promise<void>;
  createLabel(owner: string, repo: string, label: Omit<Label, 'id'>): Promise<Label>;

  // ========== Authentication Operations ==========
  verifyToken(): Promise<User>;

  // ========== Webhook Operations ==========
  verifyWebhookSignature(payload: string, signature: string, secret: string): Promise<boolean>;
  parseWebhookPayload(event: string, payload: any): Promise<WebhookPayload>;
}

// ============================================================================
// Provider Factory Type
// ============================================================================

export type GitProviderFactory = (config: ProviderConfig) => GitProvider;
