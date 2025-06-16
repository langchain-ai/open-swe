import { v4 as uuidv4 } from "uuid";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { GitHubIssueComment } from "./types.js";
import { getMessageContentFromIssue } from "./api.js";

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
