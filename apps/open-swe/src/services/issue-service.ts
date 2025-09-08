import { v4 as uuidv4 } from "uuid";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { TargetRepository, GraphConfig } from "@openswe/shared/open-swe/types";

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

export function getIssueService(_config: GraphConfig): IssueService {
  return new InMemoryIssueService();
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
