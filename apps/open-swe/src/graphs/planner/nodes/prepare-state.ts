import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@openswe/shared/open-swe/planner/types";
import { Command } from "@langchain/langgraph";
import { v4 as uuidv4 } from "uuid";
import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  isHumanMessage,
  RemoveMessage,
} from "@langchain/core/messages";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  getIssueService,
  getMessageContentFromIssue,
  getUntrackedComments,
} from "../../../services/issue-service.js";
import { filterHiddenMessages } from "../../../utils/message/filter-hidden.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@openswe/shared/constants";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { shouldCreateIssue } from "../../../utils/should-create-issue.js";

export async function prepareGraphState(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<Command> {
  if (isLocalMode(config) || !shouldCreateIssue(config)) {
    return new Command({
      update: {},
      goto: "initialize-sandbox",
    });
  }

  if (!state.githubIssueId) {
    throw new Error("No github issue id provided");
  }

  if (!state.targetRepository) {
    throw new Error("No target repository provided");
  }

  const issueService = getIssueService(config);
  const [issue, comments] = await Promise.all([
    issueService.getIssue({
      repo: state.targetRepository,
      issueId: state.githubIssueId,
    }),
    issueService.listComments({
      repo: state.targetRepository,
      issueId: state.githubIssueId,
    }),
  ]);
  if (!issue) {
    throw new Error(`Issue not found. Issue ID: ${state.githubIssueId}`);
  }

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

  // Remove all messages not marked as summaryMessage, hidden, and not human messages.
  const removedNonSummaryMessages = filterHiddenMessages(state.messages)
    .filter((m) => !m.additional_kwargs?.summaryMessage && !isHumanMessage(m))
    .map((m: BaseMessage) => new RemoveMessage({ id: m.id ?? "" }));

  // TODO: We should prob have a UI component for "Previous Task Notes" so we can surface this in the UI.
  const summaryMessage = state.contextGatheringNotes
    ? new AIMessage({
        id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
        content: `Here are the notes taken while planning for the previous task:\n${state.contextGatheringNotes}`,
        additional_kwargs: {
          summaryMessage: true,
        },
      })
    : undefined;

  const commandUpdate: PlannerGraphUpdate = {
    messages: [
      ...removedNonSummaryMessages,
      ...(summaryMessage ? [summaryMessage] : []),
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
