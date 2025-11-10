/**
 * GitLab Provider Implementation
 *
 * Implements the GitProvider interface for GitLab, using @gitbeaker SDK
 */

import { Gitlab } from "@gitbeaker/rest";
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

export class GitLabProvider implements GitProvider {
  public readonly type: ProviderType = "gitlab";
  private gitlab: InstanceType<typeof Gitlab>;

  constructor(token: string, baseUrl: string = "https://gitlab.com") {
    this.gitlab = new Gitlab({
      token,
      host: baseUrl,
    });
  }

  // ========== Helper Methods ==========

  private getProjectId(owner: string, repo: string): string {
    return `${owner}/${repo}`;
  }

  private mapUser(gitlabUser: any): User {
    return {
      id: gitlabUser.id,
      login: gitlabUser.username,
      name: gitlabUser.name,
      email: gitlabUser.email,
      avatarUrl: gitlabUser.avatar_url,
    };
  }

  private mapLabel(gitlabLabel: any): Label {
    return {
      id: gitlabLabel.id,
      name: gitlabLabel.name,
      color: gitlabLabel.color?.replace("#", "") || "000000",
      description: gitlabLabel.description,
    };
  }

  private mapRepository(gitlabProject: any): Repository {
    return {
      id: gitlabProject.id,
      owner: gitlabProject.namespace.path,
      name: gitlabProject.path,
      fullName: gitlabProject.path_with_namespace,
      defaultBranch: gitlabProject.default_branch,
      url: gitlabProject.web_url,
      cloneUrl: gitlabProject.http_url_to_repo,
      private: gitlabProject.visibility === "private",
    };
  }

  private mapBranch(gitlabBranch: any): Branch {
    return {
      name: gitlabBranch.name,
      sha: gitlabBranch.commit.id,
      protected: gitlabBranch.protected,
    };
  }

  private mapIssue(gitlabIssue: any): Issue {
    return {
      id: gitlabIssue.id,
      number: gitlabIssue.iid, // GitLab uses 'iid' for issue number
      title: gitlabIssue.title,
      body: gitlabIssue.description,
      state: gitlabIssue.state === "opened" ? "open" : "closed",
      author: this.mapUser(gitlabIssue.author),
      url: gitlabIssue.web_url,
      labels: gitlabIssue.labels?.map((label: string) => ({
        id: label,
        name: label,
        color: "000000",
      })) || [],
      createdAt: new Date(gitlabIssue.created_at),
      updatedAt: new Date(gitlabIssue.updated_at),
    };
  }

  private mapComment(gitlabNote: any): Comment {
    return {
      id: gitlabNote.id,
      body: gitlabNote.body,
      author: this.mapUser(gitlabNote.author),
      createdAt: new Date(gitlabNote.created_at),
      updatedAt: new Date(gitlabNote.updated_at),
      url: gitlabNote.noteable_type ? "" : "", // GitLab doesn't provide direct URL in API
    };
  }

  private mapMergeRequest(gitlabMR: any): PullRequest {
    return {
      id: gitlabMR.id,
      number: gitlabMR.iid, // GitLab uses 'iid' for MR number
      title: gitlabMR.title,
      body: gitlabMR.description,
      state: gitlabMR.state === "merged" ? "merged" : gitlabMR.state === "opened" ? "open" : "closed",
      draft: gitlabMR.draft || gitlabMR.work_in_progress,
      url: gitlabMR.web_url,
      headBranch: gitlabMR.source_branch,
      baseBranch: gitlabMR.target_branch,
      headSha: gitlabMR.sha,
      author: this.mapUser(gitlabMR.author),
      createdAt: new Date(gitlabMR.created_at),
      updatedAt: new Date(gitlabMR.updated_at),
      mergeable: gitlabMR.merge_status === "can_be_merged",
      labels: gitlabMR.labels?.map((label: string) => ({
        id: label,
        name: label,
        color: "000000",
      })) || [],
    };
  }

  // ========== Repository Operations ==========

  async getRepository(owner: string, repo: string): Promise<Repository> {
    const projectId = this.getProjectId(owner, repo);
    const project = await this.gitlab.Projects.show(projectId);
    return this.mapRepository(project);
  }

  async getBranch(owner: string, repo: string, branch: string): Promise<Branch> {
    const projectId = this.getProjectId(owner, repo);
    const branchData = await this.gitlab.Branches.show(projectId, branch);
    return this.mapBranch(branchData);
  }

  // ========== Issue Operations ==========

  async getIssue(owner: string, repo: string, issueNumber: number): Promise<Issue> {
    const projectId = this.getProjectId(owner, repo);

    // First, get the numeric project ID
    // Note: Issues.show() doesn't work with gitbeaker, so we use Issues.all() with filters
    const project = await this.gitlab.Projects.show(projectId);
    const numericProjectId = project.id;

    // Retry logic for race conditions (GitLab might need a moment to index new issues)
    const maxRetries = 3;
    let lastError: any;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        // Use Issues.all() with filters instead of Issues.show() because Issues.show() has a bug
        const issues = await this.gitlab.Issues.all({ projectId: numericProjectId, iids: [issueNumber] });

        if (issues.length === 0) {
          throw new Error(`404 Not Found`);
        }

        const issue = Array.isArray(issues) ? issues[0] : issues;

        return this.mapIssue(issue);
      } catch (error: any) {
        lastError = error;

        // Only retry on 404, not on other errors
        if (error.message?.includes('404') && attempt < maxRetries) {
          const waitMs = attempt * 500; // 500ms, 1000ms
          await new Promise(resolve => setTimeout(resolve, waitMs));
          continue;
        }

        throw error;
      }
    }

    throw lastError;
  }

  async createIssue(params: CreateIssueParams): Promise<Issue> {
    const projectId = this.getProjectId(params.owner, params.repo);

    try {
      const issue = await this.gitlab.Issues.create(projectId, params.title, {
        description: params.body,
        labels: params.labels?.join(","),
      });

      return this.mapIssue(issue);
    } catch (error: any) {
      throw error;
    }
  }

  async updateIssue(params: UpdateIssueParams): Promise<Issue> {
    const projectId = this.getProjectId(params.owner, params.repo);
    const updateData: any = {};

    if (params.title) updateData.title = params.title;
    if (params.body) updateData.description = params.body;
    if (params.state) updateData.state_event = params.state === "open" ? "reopen" : "close";
    if (params.labels) updateData.labels = params.labels.join(",");

    const issue = await this.gitlab.Issues.edit(projectId, params.issueNumber, updateData);
    return this.mapIssue(issue);
  }

  async listIssueComments(params: ListCommentsParams): Promise<Comment[]> {
    const projectId = this.getProjectId(params.owner, params.repo);
    const notes = await this.gitlab.IssueNotes.all(projectId, params.issueNumber);
    return Array.isArray(notes) ? notes.map((note) => this.mapComment(note)) : [];
  }

  // ========== Comment Operations ==========

  async createIssueComment(params: CreateCommentParams): Promise<Comment> {
    const projectId = this.getProjectId(params.owner, params.repo);
    const note = await this.gitlab.IssueNotes.create(projectId, params.issueNumber, params.body);
    return this.mapComment(note);
  }

  async updateIssueComment(_params: UpdateCommentParams): Promise<Comment> {
    // GitLab requires the issue number to update a note
    // This is a limitation - we may need to pass issueNumber separately
    throw new Error("GitLab requires issue number to update comments - not implemented yet");
  }

  // ========== Pull Request Operations (Merge Requests in GitLab) ==========

  async getPullRequest(owner: string, repo: string, pullNumber: number): Promise<PullRequest> {
    const projectId = this.getProjectId(owner, repo);
    const mr = await this.gitlab.MergeRequests.show(projectId, pullNumber);
    return this.mapMergeRequest(mr);
  }

  async createPullRequest(params: CreatePullRequestParams): Promise<PullRequest> {
    const projectId = this.getProjectId(params.owner, params.repo);
    const mr = await this.gitlab.MergeRequests.create(projectId, params.head, params.base, params.title, {
      description: params.body,
    });
    return this.mapMergeRequest(mr);
  }

  async updatePullRequest(params: UpdatePullRequestParams): Promise<PullRequest> {
    const projectId = this.getProjectId(params.owner, params.repo);
    const updateData: any = {};

    if (params.title) updateData.title = params.title;
    if (params.body) updateData.description = params.body;
    if (params.state) {
      updateData.state_event = params.state === "closed" ? "close" : "reopen";
    }

    const mr = await this.gitlab.MergeRequests.edit(projectId, params.pullNumber, updateData);
    return this.mapMergeRequest(mr);
  }

  async markPullRequestReady(params: MarkPullRequestReadyParams): Promise<PullRequest> {
    const projectId = this.getProjectId(params.owner, params.repo);

    // Remove draft status by removing "Draft:" or "WIP:" prefix from title
    const mr = await this.gitlab.MergeRequests.show(projectId, params.pullNumber);
    const newTitle = mr.title.replace(/^(Draft:|WIP:)\s*/i, "");

    const updatedMr = await this.gitlab.MergeRequests.edit(projectId, params.pullNumber, {
      title: newTitle,
    });

    return this.mapMergeRequest(updatedMr);
  }

  // ========== Review Comment Operations ==========

  async createReviewCommentReply(params: CreateReviewCommentReplyParams): Promise<Comment> {
    const projectId = this.getProjectId(params.owner, params.repo);

    // GitLab handles discussions differently - create a new note in the discussion
    const note = await this.gitlab.MergeRequestNotes.create(
      projectId,
      params.pullNumber,
      params.body,
      {
        // GitLab can reply to discussions using discussion_id
        // but we need the discussion_id, not just the note_id
      }
    );

    return this.mapComment(note);
  }

  // ========== Label Operations ==========

  async addLabels(owner: string, repo: string, issueNumber: number, labels: string[]): Promise<void> {
    const projectId = this.getProjectId(owner, repo);

    // Get current labels
    const issue = await (this.gitlab.Issues as any).show(projectId, issueNumber);
    const currentLabels = Array.isArray(issue.labels) ? issue.labels : [];

    // Merge with new labels
    const allLabels = [...new Set([...currentLabels, ...labels])];

    await (this.gitlab.Issues as any).edit(projectId, issueNumber, {
      labels: allLabels.join(","),
    });
  }

  async removeLabel(owner: string, repo: string, issueNumber: number, label: string): Promise<void> {
    const projectId = this.getProjectId(owner, repo);

    // Get current labels
    const issue = await (this.gitlab.Issues as any).show(projectId, issueNumber);
    const currentLabels = Array.isArray(issue.labels) ? issue.labels : [];

    // Remove the label
    const newLabels = currentLabels.filter((l: string) => l !== label);

    await (this.gitlab.Issues as any).edit(projectId, issueNumber, {
      labels: newLabels.join(","),
    });
  }

  async createLabel(owner: string, repo: string, label: Omit<Label, "id">): Promise<Label> {
    const projectId = this.getProjectId(owner, repo);

    const newLabel = await this.gitlab.ProjectLabels.create(projectId, label.name, `#${label.color}`, {
      description: label.description,
    });

    return this.mapLabel(newLabel);
  }

  // ========== Authentication Operations ==========

  async verifyToken(): Promise<User> {
    try {
      const user = await this.gitlab.Users.showCurrentUser();
      return this.mapUser(user);
    } catch (error: any) {
      throw error;
    }
  }

  // ========== Webhook Operations ==========

  async verifyWebhookSignature(_payload: string, signature: string, secret: string): Promise<boolean> {
    // GitLab uses a token-based authentication for webhooks, not HMAC
    // The token is sent in the X-Gitlab-Token header
    return signature === secret;
  }

  async parseWebhookPayload(_event: string, payload: any): Promise<WebhookPayload> {
    const eventType = this.mapGitLabEventType(payload.object_kind, payload.object_attributes?.action);

    // Map GitLab webhook to common format
    const repository = payload.project ? this.mapRepository(payload.project) : undefined;
    const sender = payload.user ? this.mapUser(payload.user) : undefined;

    return {
      eventType,
      provider: "gitlab",
      repository: repository!,
      sender: sender!,
      issue: payload.object_kind === "issue" ? this.mapIssue({
        id: payload.object_attributes.id,
        iid: payload.object_attributes.iid,
        title: payload.object_attributes.title,
        description: payload.object_attributes.description,
        state: payload.object_attributes.state,
        author: payload.user,
        web_url: payload.object_attributes.url,
        labels: payload.labels?.map((l: any) => l.title) || [],
        created_at: payload.object_attributes.created_at,
        updated_at: payload.object_attributes.updated_at,
      }) : undefined,
      pullRequest: payload.object_kind === "merge_request" ? this.mapMergeRequest({
        id: payload.object_attributes.id,
        iid: payload.object_attributes.iid,
        title: payload.object_attributes.title,
        description: payload.object_attributes.description,
        state: payload.object_attributes.state,
        draft: payload.object_attributes.draft || payload.object_attributes.work_in_progress,
        web_url: payload.object_attributes.url,
        source_branch: payload.object_attributes.source_branch,
        target_branch: payload.object_attributes.target_branch,
        sha: payload.object_attributes.last_commit?.id,
        author: payload.user,
        created_at: payload.object_attributes.created_at,
        updated_at: payload.object_attributes.updated_at,
        merge_status: payload.object_attributes.merge_status,
        labels: payload.labels?.map((l: any) => l.title) || [],
      }) : undefined,
      comment: payload.object_kind === "note" ? this.mapComment({
        id: payload.object_attributes.id,
        body: payload.object_attributes.note,
        author: payload.user,
        created_at: payload.object_attributes.created_at,
        updated_at: payload.object_attributes.updated_at,
        noteable_type: payload.object_attributes.noteable_type,
      }) : undefined,
      label: payload.label ? this.mapLabel(payload.label) : undefined,
      rawPayload: payload,
    };
  }

  private mapGitLabEventType(objectKind: string, action?: string): any {
    const mapping: Record<string, any> = {
      "issue.opened": "issue.opened",
      "issue.update": "issue.labeled", // May need refinement
      "note.issue": "issue.comment",
      "merge_request.opened": "pull_request.opened",
      "note.merge_request": "pull_request.comment",
    };

    const key = action ? `${objectKind}.${action}` : objectKind;
    return mapping[key] || objectKind;
  }
}
