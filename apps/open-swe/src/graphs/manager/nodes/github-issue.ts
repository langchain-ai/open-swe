import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { ManagerGraphState, ManagerGraphUpdate } from "../types.js";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import {
  BaseMessage,
  HumanMessage,
  isHumanMessage,
  RemoveMessage,
} from "@langchain/core/messages";
import {
  createIssue,
  createIssueComment,
  getIssue,
  getIssueComments,
} from "../../../utils/github/api.js";
import {
  GitHubIssue,
  GitHubIssueComment,
} from "../../../utils/github/types.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { extractTasksFromIssueContent } from "../../../utils/task-string-extraction.js";

const getMessageContentFromIssue = (
  issue: GitHubIssue | GitHubIssueComment,
) => {
  if ("title" in issue) {
    return `[original issue]\n**${issue.title}**\n${issue.body}`;
  }
  return `[issue comment]\n${issue.body}`;
};

const messagesListHasOriginalIssue = (messages: BaseMessage[]) => {
  return messages.some((message) => {
    return message.additional_kwargs?.isOriginalIssue;
  });
};

const filterUntrackedComments = (
  messages: BaseMessage[],
  issueComments: GitHubIssueComment[],
) => {
  return issueComments.filter((comment) => {
    return !messages.some((message) => {
      return message.additional_kwargs?.githubIssueCommentId === comment.id;
    });
  });
};

/**
 * The initialize function will do nothing if there's already a human message
 * in the state. If not, it will attempt to get the human message from the GitHub issue.
 */
export async function initializeGithubIssue(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<ManagerGraphUpdate> {
  if (state.messages.length && state.messages.some(isHumanMessage)) {
    // If there are messages, & at least one is a human message, do nothing.
    return {};
  }
  // If there are no messages, ensure there's a GitHub issue to fetch the message from.
  if (!state.githubIssueId) {
    throw new Error("GitHub issue ID not provided");
  }
  if (!state.targetRepository) {
    throw new Error("Target repository not provided");
  }
  const { githubInstallationToken } =
    getGitHubTokensFromConfig(config);

    const issue = await getIssue({
      owner: state.targetRepository.owner,
      repo: state.targetRepository.repo,
      issueNumber: state.githubIssueId,
      githubInstallationToken,
    });
    if (!issue) {
      throw new Error("Issue not found");
    }

    const newMessage = new HumanMessage({
      id: uuidv4(),
      content: getMessageContentFromIssue(issue),
      additional_kwargs: {
        githubIssueId: state.githubIssueId,
        isOriginalIssue: true,
      }
    });

    return {
      messages: [newMessage],
    }
}
