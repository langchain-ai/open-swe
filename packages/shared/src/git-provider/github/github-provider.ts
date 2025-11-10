/**
 * GitHub Provider Implementation
 *
 * Implements the GitProvider interface for GitHub, wrapping Octokit SDK
 */

import { Octokit } from "@octokit/rest";
import { createHmac } from "crypto";
import type {
  GitProvider,
  ProviderType,
  Repository,
  Branch,
  Issue,
  Comment,
  PullRequest,
  Label,
  User,
  CreatePullRequestParams,
  UpdatePullRequestParams,
  MarkPullRequestReadyParams,
  CreateIssueParams,
  UpdateIssueParams,
  CreateCommentParams,
  UpdateCommentParams,
  CreateReviewCommentReplyParams,
  ListCommentsParams,
  WebhookPayload,
} from "../types.js";

export class GitHubProvider implements GitProvider {
  public readonly type: ProviderType = "github";
  private octokit: Octokit;

  constructor(token: string) {
    this.octokit = new Octokit({ auth: token });
  }

  // ========== Helper Methods ==========

  private mapUser(githubUser: any): User {
    return {
      id: githubUser.id,
      login: githubUser.login,
      name: githubUser.name,
      email: githubUser.email,
      avatarUrl: githubUser.avatar_url,
    };
  }

  private mapLabel(githubLabel: any): Label {
    return {
      id: githubLabel.id,
      name: githubLabel.name,
      color: githubLabel.color,
      description: githubLabel.description,
    };
  }

  private mapRepository(githubRepo: any): Repository {
    return {
      id: githubRepo.id,
      owner: githubRepo.owner.login,
      name: githubRepo.name,
      fullName: githubRepo.full_name,
      defaultBranch: githubRepo.default_branch,
      url: githubRepo.html_url,
      cloneUrl: githubRepo.clone_url,
      private: githubRepo.private,
    };
  }

  private mapBranch(githubBranch: any): Branch {
    return {
      name: githubBranch.name,
      sha: githubBranch.commit.sha,
      protected: githubBranch.protected,
    };
  }

  private mapIssue(githubIssue: any): Issue {
    return {
      id: githubIssue.id,
      number: githubIssue.number,
      title: githubIssue.title,
      body: githubIssue.body,
      state: githubIssue.state,
      author: this.mapUser(githubIssue.user),
      url: githubIssue.html_url,
      labels: githubIssue.labels.map((label: any) => this.mapLabel(label)),
      createdAt: new Date(githubIssue.created_at),
      updatedAt: new Date(githubIssue.updated_at),
    };
  }

  private mapComment(githubComment: any): Comment {
    return {
      id: githubComment.id,
      body: githubComment.body,
      author: this.mapUser(githubComment.user),
      createdAt: new Date(githubComment.created_at),
      updatedAt: new Date(githubComment.updated_at),
      url: githubComment.html_url,
    };
  }

  private mapPullRequest(githubPR: any): PullRequest {
    return {
      id: githubPR.id,
      number: githubPR.number,
      title: githubPR.title,
      body: githubPR.body,
      state: githubPR.merged_at ? "merged" : githubPR.state,
      draft: githubPR.draft,
      url: githubPR.html_url,
      headBranch: githubPR.head.ref,
      baseBranch: githubPR.base.ref,
      headSha: githubPR.head.sha,
      author: this.mapUser(githubPR.user),
      createdAt: new Date(githubPR.created_at),
      updatedAt: new Date(githubPR.updated_at),
      mergeable: githubPR.mergeable,
      labels: githubPR.labels?.map((label: any) => this.mapLabel(label)) || [],
    };
  }

  // ========== Repository Operations ==========

  async getRepository(owner: string, repo: string): Promise<Repository> {
    const { data } = await this.octokit.repos.get({ owner, repo });
    return this.mapRepository(data);
  }

  async getBranch(owner: string, repo: string, branch: string): Promise<Branch> {
    const { data } = await this.octokit.repos.getBranch({ owner, repo, branch });
    return this.mapBranch(data);
  }

  // ========== Issue Operations ==========

  async getIssue(owner: string, repo: string, issueNumber: number): Promise<Issue> {
    const { data } = await this.octokit.issues.get({
      owner,
      repo,
      issue_number: issueNumber,
    });
    return this.mapIssue(data);
  }

  async createIssue(params: CreateIssueParams): Promise<Issue> {
    const { data } = await this.octokit.issues.create({
      owner: params.owner,
      repo: params.repo,
      title: params.title,
      body: params.body,
      labels: params.labels,
    });
    return this.mapIssue(data);
  }

  async updateIssue(params: UpdateIssueParams): Promise<Issue> {
    const { data } = await this.octokit.issues.update({
      owner: params.owner,
      repo: params.repo,
      issue_number: params.issueNumber,
      ...(params.title && { title: params.title }),
      ...(params.body && { body: params.body }),
      ...(params.state && { state: params.state }),
      ...(params.labels && { labels: params.labels }),
    });
    return this.mapIssue(data);
  }

  async listIssueComments(params: ListCommentsParams): Promise<Comment[]> {
    const { data } = await this.octokit.issues.listComments({
      owner: params.owner,
      repo: params.repo,
      issue_number: params.issueNumber,
    });
    return data.map((comment) => this.mapComment(comment));
  }

  // ========== Comment Operations ==========

  async createIssueComment(params: CreateCommentParams): Promise<Comment> {
    const { data } = await this.octokit.issues.createComment({
      owner: params.owner,
      repo: params.repo,
      issue_number: params.issueNumber,
      body: params.body,
    });
    return this.mapComment(data);
  }

  async updateIssueComment(params: UpdateCommentParams): Promise<Comment> {
    const { data } = await this.octokit.issues.updateComment({
      owner: params.owner,
      repo: params.repo,
      comment_id: params.commentId,
      body: params.body,
    });
    return this.mapComment(data);
  }

  // ========== Pull Request Operations ==========

  async getPullRequest(owner: string, repo: string, pullNumber: number): Promise<PullRequest> {
    const { data } = await this.octokit.pulls.get({
      owner,
      repo,
      pull_number: pullNumber,
    });
    return this.mapPullRequest(data);
  }

  async createPullRequest(params: CreatePullRequestParams): Promise<PullRequest> {
    const { data } = await this.octokit.pulls.create({
      owner: params.owner,
      repo: params.repo,
      title: params.title,
      body: params.body,
      head: params.head,
      base: params.base,
      draft: params.draft || false,
    });
    return this.mapPullRequest(data);
  }

  async updatePullRequest(params: UpdatePullRequestParams): Promise<PullRequest> {
    const { data } = await this.octokit.pulls.update({
      owner: params.owner,
      repo: params.repo,
      pull_number: params.pullNumber,
      ...(params.title && { title: params.title }),
      ...(params.body && { body: params.body }),
      ...(params.state && { state: params.state }),
    });
    return this.mapPullRequest(data);
  }

  async markPullRequestReady(params: MarkPullRequestReadyParams): Promise<PullRequest> {
    // Get PR to fetch node_id
    const { data: pr } = await this.octokit.pulls.get({
      owner: params.owner,
      repo: params.repo,
      pull_number: params.pullNumber,
    });

    // Use GraphQL to mark as ready
    await this.octokit.graphql(
      `
      mutation MarkPullRequestReadyForReview($pullRequestId: ID!) {
        markPullRequestReadyForReview(input: {
          pullRequestId: $pullRequestId
        }) {
          clientMutationId
          pullRequest {
            id
            number
            isDraft
          }
        }
      }
    `,
      {
        pullRequestId: pr.node_id,
      }
    );

    // Fetch updated PR
    const { data: updatedPR } = await this.octokit.pulls.get({
      owner: params.owner,
      repo: params.repo,
      pull_number: params.pullNumber,
    });

    return this.mapPullRequest(updatedPR);
  }

  // ========== Review Comment Operations ==========

  async createReviewCommentReply(params: CreateReviewCommentReplyParams): Promise<Comment> {
    const { data } = await this.octokit.pulls.createReplyForReviewComment({
      owner: params.owner,
      repo: params.repo,
      pull_number: params.pullNumber,
      comment_id: params.inReplyTo,
      body: params.body,
    });
    return this.mapComment(data);
  }

  // ========== Label Operations ==========

  async addLabels(owner: string, repo: string, issueNumber: number, labels: string[]): Promise<void> {
    await this.octokit.issues.addLabels({
      owner,
      repo,
      issue_number: issueNumber,
      labels,
    });
  }

  async removeLabel(owner: string, repo: string, issueNumber: number, label: string): Promise<void> {
    await this.octokit.issues.removeLabel({
      owner,
      repo,
      issue_number: issueNumber,
      name: label,
    });
  }

  async createLabel(owner: string, repo: string, label: Omit<Label, "id">): Promise<Label> {
    const { data } = await this.octokit.issues.createLabel({
      owner,
      repo,
      name: label.name,
      color: label.color,
      description: label.description,
    });
    return this.mapLabel(data);
  }

  // ========== Authentication Operations ==========

  async verifyToken(): Promise<User> {
    const { data } = await this.octokit.users.getAuthenticated();
    return this.mapUser(data);
  }

  // ========== Webhook Operations ==========

  async verifyWebhookSignature(payload: string, signature: string, secret: string): Promise<boolean> {
    const hmac = createHmac("sha256", secret);
    hmac.update(payload);
    const digest = `sha256=${hmac.digest("hex")}`;
    return digest === signature;
  }

  async parseWebhookPayload(event: string, payload: any): Promise<WebhookPayload> {
    // This is a simplified implementation
    // In production, you'd want more comprehensive event mapping
    const eventType = this.mapGitHubEventType(event, payload.action);

    return {
      eventType,
      provider: "github",
      repository: this.mapRepository(payload.repository),
      sender: this.mapUser(payload.sender),
      issue: payload.issue ? this.mapIssue(payload.issue) : undefined,
      pullRequest: payload.pull_request ? this.mapPullRequest(payload.pull_request) : undefined,
      comment: payload.comment ? this.mapComment(payload.comment) : undefined,
      label: payload.label ? this.mapLabel(payload.label) : undefined,
      installation: payload.installation
        ? {
            id: payload.installation.id,
            name: payload.installation.account.login,
            type: payload.installation.account.type === "Organization" ? "organization" : "user",
            avatarUrl: payload.installation.account.avatar_url,
          }
        : undefined,
      rawPayload: payload,
    };
  }

  private mapGitHubEventType(event: string, action: string): any {
    const mapping: Record<string, any> = {
      "issues.labeled": "issue.labeled",
      "issues.opened": "issue.opened",
      "issue_comment.created": "issue.comment",
      "pull_request.opened": "pull_request.opened",
      "pull_request_review.submitted": "pull_request.review",
      "pull_request_review_comment.created": "pull_request.review_comment",
    };

    const key = `${event}.${action}`;
    return mapping[key] || event;
  }
}
