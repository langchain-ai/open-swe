import { v4 as uuidv4 } from "uuid";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { TargetRepository, GraphConfig } from "@openswe/shared/open-swe/types";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { getGitHubTokensFromConfig } from "../utils/github-tokens.js";
import {
  createIssue,
  createIssueComment,
  getIssue,
  getIssueComments,
} from "../utils/github/api.js";

export interface Issue {
  id: string;
  title: string;
  body: string;
}

export interface IssueComment {
  id: string;
  body: string;
}

export interface IssueService {
  createIssue(input: {
    repo: TargetRepository;
    title: string;
    body: string;
  }): Promise<Issue | null>;
  createComment(input: {
    repo: TargetRepository;
    issueId: number | string;
    body: string;
  }): Promise<IssueComment | null>;
  listComments(input: {
    repo: TargetRepository;
    issueId: number | string;
  }): Promise<IssueComment[]>;
  getIssue(input: {
    repo: TargetRepository;
    issueId: number | string;
  }): Promise<Issue | null>;
}

class InMemoryIssueService implements IssueService {
  private issues = new Map<
    string,
    { issue: Issue; comments: IssueComment[] }
  >();

  async createIssue(input: {
    repo: TargetRepository;
    title: string;
    body: string;
  }): Promise<Issue | null> {
    const id = uuidv4();
    const issue: Issue = { id, title: input.title, body: input.body };
    this.issues.set(id, { issue, comments: [] });
    return issue;
  }

  async createComment(input: {
    repo: TargetRepository;
    issueId: number | string;
    body: string;
  }): Promise<IssueComment | null> {
    const id = uuidv4();
    const comment: IssueComment = { id, body: input.body };
    const existing = this.issues.get(String(input.issueId));
    if (existing) {
      existing.comments.push(comment);
    }
    return comment;
  }

  async listComments(input: {
    repo: TargetRepository;
    issueId: number | string;
  }): Promise<IssueComment[]> {
    return this.issues.get(String(input.issueId))?.comments ?? [];
  }

  async getIssue(input: {
    repo: TargetRepository;
    issueId: number | string;
  }): Promise<Issue | null> {
    return this.issues.get(String(input.issueId))?.issue ?? null;
  }
}

class GitHubIssueService implements IssueService {
  constructor(
    private tokens: {
      githubAccessToken: string;
      githubInstallationToken: string;
    },
  ) {}

  async createIssue(input: {
    repo: TargetRepository;
    title: string;
    body: string;
  }): Promise<Issue | null> {
    const issue = await createIssue({
      owner: input.repo.owner,
      repo: input.repo.repo,
      title: input.title,
      body: input.body,
      githubAccessToken: this.tokens.githubAccessToken,
    });
    if (!issue) return null;
    return {
      id: String(issue.number),
      title: issue.title,
      body: issue.body ?? "",
    };
  }

  async createComment(input: {
    repo: TargetRepository;
    issueId: number | string;
    body: string;
  }): Promise<IssueComment | null> {
    const comment = await createIssueComment({
      owner: input.repo.owner,
      repo: input.repo.repo,
      issueNumber: Number(input.issueId),
      body: input.body,
      githubToken: this.tokens.githubAccessToken,
    });
    if (!comment) return null;
    return { id: String(comment.id), body: comment.body ?? "" };
  }

  async listComments(input: {
    repo: TargetRepository;
    issueId: number | string;
  }): Promise<IssueComment[]> {
    const comments = await getIssueComments({
      owner: input.repo.owner,
      repo: input.repo.repo,
      issueNumber: Number(input.issueId),
      githubInstallationToken: this.tokens.githubInstallationToken,
      filterBotComments: true,
    });
    return (comments ?? []).map((c) => ({
      id: String(c.id),
      body: c.body ?? "",
    }));
  }

  async getIssue(input: {
    repo: TargetRepository;
    issueId: number | string;
  }): Promise<Issue | null> {
    const issue = await getIssue({
      owner: input.repo.owner,
      repo: input.repo.repo,
      issueNumber: Number(input.issueId),
      githubInstallationToken: this.tokens.githubInstallationToken,
    });
    if (!issue) return null;
    return {
      id: String(issue.number),
      title: issue.title,
      body: issue.body ?? "",
    };
  }
}

export function getIssueService(config: GraphConfig): IssueService {
  if (isLocalMode(config)) {
    return new InMemoryIssueService();
  }
  const tokens = getGitHubTokensFromConfig(config);
  return new GitHubIssueService(tokens);
}

/**
 * Utility to get content for an issue or comment as a message body
 */
export function getMessageContentFromIssue(
  issue: Issue | IssueComment,
): string {
  if ((issue as Issue).title !== undefined) {
    return `[original issue]\n**${(issue as Issue).title}**\n${(issue as Issue).body}`;
  }
  return `[issue comment]\n${issue.body}`;
}

/**
 * Get comments that are not yet represented as messages
 */
export function getUntrackedComments(
  existingMessages: BaseMessage[],
  issueId: number,
  comments: IssueComment[],
): BaseMessage[] {
  const humanMessages = existingMessages.filter(
    (m) => isHumanMessage(m) && !m.additional_kwargs?.isOriginalIssue,
  );
  return comments
    .filter(
      (c) =>
        !humanMessages.some(
          (m) => m.additional_kwargs?.githubIssueCommentId === c.id,
        ),
    )
    .map(
      (c) =>
        new HumanMessage({
          id: uuidv4(),
          content: getMessageContentFromIssue(c),
          additional_kwargs: {
            githubIssueId: issueId,
            githubIssueCommentId: c.id,
          },
        }),
    );
}

export async function getMissingMessages(
  issueService: IssueService,
  input: {
    messages: BaseMessage[];
    issueId: number;
    repo: TargetRepository;
  },
): Promise<BaseMessage[]> {
  const [issue, comments] = await Promise.all([
    issueService.getIssue({ repo: input.repo, issueId: input.issueId }),
    issueService.listComments({ repo: input.repo, issueId: input.issueId }),
  ]);
  if (!issue && !comments.length) {
    return [];
  }
  const isIssueMessageTracked = issue
    ? input.messages.some(
        (m) =>
          isHumanMessage(m) &&
          m.additional_kwargs?.isOriginalIssue &&
          m.additional_kwargs?.githubIssueId === input.issueId,
      )
    : false;
  let issueMessage: HumanMessage | null = null;
  if (issue && !isIssueMessageTracked) {
    issueMessage = new HumanMessage({
      id: uuidv4(),
      content: getMessageContentFromIssue(issue),
      additional_kwargs: {
        githubIssueId: input.issueId,
        isOriginalIssue: true,
      },
    });
  }
  const untrackedCommentMessages = comments.length
    ? getUntrackedComments(input.messages, input.issueId, comments)
    : [];
  return [...(issueMessage ? [issueMessage] : []), ...untrackedCommentMessages];
}
