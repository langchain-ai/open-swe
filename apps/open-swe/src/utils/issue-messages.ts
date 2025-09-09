import { v4 as uuidv4 } from "uuid";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { Issue, IssueComment } from "../services/issue-service.js";

export const DETAILS_OPEN_TAG = "<details>";
export const DETAILS_CLOSE_TAG = "</details>";

export function getUntrackedComments(
  existingMessages: BaseMessage[],
  issueId: number,
  comments: IssueComment[],
): BaseMessage[] {
  // Get all human messages which contain issue comment content. Exclude the original issue message.
  const humanMessages = existingMessages.filter(
    (m) => isHumanMessage(m) && !m.additional_kwargs?.isOriginalIssue,
  );
  // Iterate over the comments, and filter out any comment already tracked by a message.
  // Then, map to create new human message(s).
  const untrackedCommentMessages = comments
    .filter(
      (c) =>
        !humanMessages.some(
          (m) => m.additional_kwargs?.issueCommentId === c.id,
        ),
    )
    .map(
      (c) =>
        new HumanMessage({
          id: uuidv4(),
          content: getMessageContentFromIssue(c),
          additional_kwargs: {
            issueId,
            issueCommentId: c.id,
          },
        }),
    );

  return untrackedCommentMessages;
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
    body.substring(
      body.indexOf(DETAILS_OPEN_TAG) + DETAILS_OPEN_TAG.length,
      body.indexOf(DETAILS_CLOSE_TAG),
    ),
  );
  return bodyWithoutDetails;
}

export function getMessageContentFromIssue(
  issue: Issue | IssueComment,
): string {
  if ("title" in issue) {
    const formattedBody = extractContentWithoutDetailsFromIssueBody(
      issue.body ?? "",
    );
    return `[original issue]\n**${issue.title}**\n${formattedBody}`;
  }
  return `[issue comment]\n${issue.body}`;
}
