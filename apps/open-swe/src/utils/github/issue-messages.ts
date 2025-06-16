import { v4 as uuidv4 } from "uuid";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { GitHubIssueComment } from "./types.js";
import { getIssueComments, getMessageContentFromIssue } from "./api.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { getGitHubTokensFromConfig } from "../github-tokens.js";

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
  const { githubInstallationToken } = getGitHubTokensFromConfig(config);
  const comments = await getIssueComments({
    owner: input.targetRepository.owner,
    repo: input.targetRepository.repo,
    issueNumber: input.githubIssueId,
    githubInstallationToken,
    filterBotComments: true,
  });
  if (!comments?.length) {
    return [];
  }

  return getUntrackedComments(input.messages, input.githubIssueId, comments);
}
