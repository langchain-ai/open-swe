import { v4 as uuidv4 } from "uuid";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { GitHubIssue, GitHubIssueComment } from "./types.js";
import { getIssue, getIssueComments } from "./api.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { getGitHubTokensFromConfig } from "../github-tokens.js";
import { DETAILS_OPEN_TAG } from "./issue-task.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";

export function getUntrackedComments(
  existingMessages: BaseMessage[],
  githubIssueId: number,
  comments: GitHubIssueComment[],
): BaseMessage[] {
  // Get all human messages which contain github comment content. Exclude the original issue message.
  const humanMessages = existingMessages.filter(
    (m) => isHumanMessage(m) && !m.additional_kwargs?.isOriginalIssue,
  );
  // Iterate over the comments, and filter out any comment already tracked by a message.
  // Then, map to create new human message(s).
  const untrackedCommentMessages = comments
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
            githubIssueId,
            githubIssueCommentId: c.id,
          },
        }),
    );

  return untrackedCommentMessages;
}

type GetMissingMessagesInput = {
  messages: BaseMessage[];
  githubIssueId: number;
  targetRepository: TargetRepository;
};

export async function getMissingMessages(
  input: GetMissingMessagesInput,
  config: GraphConfig,
): Promise<BaseMessage[]> {
  if (isLocalMode(config)) {
    return [];
  }

  const { githubInstallationToken } = getGitHubTokensFromConfig(config);
  const [issue, comments] = await Promise.all([
    getIssue({
      owner: input.targetRepository.owner,
      repo: input.targetRepository.repo,
      issueNumber: input.githubIssueId,
      githubInstallationToken,
    }),
    getIssueComments({
      owner: input.targetRepository.owner,
      repo: input.targetRepository.repo,
      issueNumber: input.githubIssueId,
      githubInstallationToken,
      filterBotComments: true,
    }),
  ]);
  if (!issue && !comments?.length) {
    return [];
  }

  const isIssueMessageTracked = issue
    ? input.messages.some(
        (m) =>
          isHumanMessage(m) &&
          m.additional_kwargs?.isOriginalIssue &&
          m.additional_kwargs?.githubIssueId === input.githubIssueId,
      )
    : false;
  let issueMessage: HumanMessage | null = null;
  if (issue && !isIssueMessageTracked) {
    issueMessage = new HumanMessage({
      id: uuidv4(),
      content: getMessageContentFromIssue(issue),
      additional_kwargs: {
        githubIssueId: input.githubIssueId,
        isOriginalIssue: true,
      },
    });
  }

  const untrackedCommentMessages = comments?.length
    ? getUntrackedComments(input.messages, input.githubIssueId, comments)
    : [];

  return [...(issueMessage ? [issueMessage] : []), ...untrackedCommentMessages];
}

export const DEFAULT_ISSUE_TITLE = "New Open SWE Request";
export const ISSUE_TITLE_OPEN_TAG = "<open-swe-issue-title>";
export const ISSUE_TITLE_CLOSE_TAG = "</open-swe-issue-title>";
export const ISSUE_CONTENT_OPEN_TAG = "<open-swe-issue-content>";
export const ISSUE_CONTENT_CLOSE_TAG = "</open-swe-issue-content>";

export function extractIssueTitleAndContentFromMessage(content: string) {
  let messageTitle: string | null = null;
  let messageContent = content;
  if (
    content.includes(ISSUE_TITLE_OPEN_TAG) &&
    content.includes(ISSUE_TITLE_CLOSE_TAG)
  ) {
    messageTitle = content.substring(
      content.indexOf(ISSUE_TITLE_OPEN_TAG) + ISSUE_TITLE_OPEN_TAG.length,
      content.indexOf(ISSUE_TITLE_CLOSE_TAG),
    );
  }
  if (
    content.includes(ISSUE_CONTENT_OPEN_TAG) &&
    content.includes(ISSUE_CONTENT_CLOSE_TAG)
  ) {
    messageContent = content.substring(
      content.indexOf(ISSUE_CONTENT_OPEN_TAG) + ISSUE_CONTENT_OPEN_TAG.length,
      content.indexOf(ISSUE_CONTENT_CLOSE_TAG),
    );
  }
  return { title: messageTitle, content: messageContent };
}

export function formatContentForIssueBody(body: string): string {
  return `${ISSUE_CONTENT_OPEN_TAG}${body}${ISSUE_CONTENT_CLOSE_TAG}`;
}

function extractContentFromIssueBody(body: string): string {
  if (
    !body.includes(ISSUE_CONTENT_OPEN_TAG) ||
    !body.includes(ISSUE_CONTENT_CLOSE_TAG)
  ) {
    return body;
  }

  return body.substring(
    body.indexOf(ISSUE_CONTENT_OPEN_TAG) + ISSUE_CONTENT_OPEN_TAG.length,
    body.indexOf(ISSUE_CONTENT_CLOSE_TAG),
  );
}

export function extractContentWithoutDetailsFromIssueBody(
  body: string,
): string {
  if (!body.includes(DETAILS_OPEN_TAG)) {
    return extractContentFromIssueBody(body);
  }

  const bodyWithoutDetails = extractContentFromIssueBody(
    body.split(DETAILS_OPEN_TAG)[0],
  );
  return bodyWithoutDetails;
}

export function getMessageContentFromIssue(
  issue: GitHubIssue | GitHubIssueComment,
): string {
  if ("title" in issue) {
    return `[original issue]\n**${issue.title}**\n${extractContentFromIssueBody(issue.body ?? "")}`;
  }
  return `[issue comment]\n${issue.body}`;
}
