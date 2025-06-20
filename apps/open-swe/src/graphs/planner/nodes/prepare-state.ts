import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { Command, END } from "@langchain/langgraph";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import { getIssue, getIssueComments } from "../../../utils/github/api.js";
import { v4 as uuidv4 } from "uuid";
import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  isHumanMessage,
  RemoveMessage,
} from "@langchain/core/messages";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  getMessageContentFromIssue,
  getUntrackedComments,
} from "../../../utils/github/issue-messages.js";
import { filterHiddenMessages } from "../../../utils/message/filter-hidden.js";

export async function prepareGraphState(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<Command> {
  if (!state.githubIssueId) {
    throw new Error("No github issue id provided");
  }
  if (!state.targetRepository) {
    throw new Error("No target repository provided");
  }
  const { githubInstallationToken } = getGitHubTokensFromConfig(config);
  const baseGetIssueInputs = {
    owner: state.targetRepository.owner,
    repo: state.targetRepository.repo,
    issueNumber: state.githubIssueId,
    githubInstallationToken,
  };
  const [issue, comments] = await Promise.all([
    getIssue(baseGetIssueInputs),
    getIssueComments({
      ...baseGetIssueInputs,
      filterBotComments: true,
    }),
  ]);
  if (!issue) {
    throw new Error(`Issue not found. Issue ID: ${state.githubIssueId}`);
  }

  // Ensure the main issue & all comments are included in the state;

  // If the messages state is empty, we can just include all comments as human messages.
  if (!state.messages?.length) {
    const commandUpdate: PlannerGraphUpdate = {
      messages: [
        new HumanMessage({
          id: uuidv4(),
          content: getMessageContentFromIssue(issue),
          additional_kwargs: {
            githubIssueId: state.githubIssueId,
            isOriginalIssue: true,
          },
        }),
        ...(comments ?? []).map(
          (comment) =>
            new HumanMessage({
              id: uuidv4(),
              content: getMessageContentFromIssue(comment),
              additional_kwargs: {
                githubIssueId: state.githubIssueId,
                githubIssueCommentId: comment.id,
              },
            }),
        ),
      ],
    };
    return new Command({
      update: commandUpdate,
      goto: "initialize-sandbox",
    });
  }

  const untrackedComments = getUntrackedComments(
    state.messages,
    state.githubIssueId,
    comments ?? [],
  );
  if (!untrackedComments?.length) {
    // If there are already messages in the state, and no comments, we can assume the issue is already handled.
    return new Command({
      goto: END,
    });
  }

  // Remove all messages not marked as summaryMessage, hidden, and not human messages.
  const removedNonSummaryMessages = filterHiddenMessages(state.messages)
    .filter((m) => !m.additional_kwargs?.summaryMessage && !isHumanMessage(m))
    .map((m: BaseMessage) => new RemoveMessage({ id: m.id ?? "" }));
  const summaryMessage = new AIMessage({
    id: uuidv4(),
    content: state.contextGatheringNotes,
    additional_kwargs: {
      summaryMessage: true,
    },
  });
  const commandUpdate: PlannerGraphUpdate = {
    messages: [
      ...removedNonSummaryMessages,
      summaryMessage,
      ...untrackedComments,
    ],
    // Reset plan context summary as it's now included in the messages array.
    contextGatheringNotes: "",
  };

  return new Command({
    update: commandUpdate,
    goto: "initialize-sandbox",
  });
}
