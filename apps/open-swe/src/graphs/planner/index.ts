import { v4 as uuidv4 } from "uuid";
import { Command, END, START, StateGraph } from "@langchain/langgraph";
import {
  PlannerGraphState,
  PlannerGraphStateObj,
  PlannerGraphUpdate,
} from "./types.js";
import {
  GraphConfig,
  GraphConfiguration,
} from "@open-swe/shared/open-swe/types";
import {
  generateAction,
  generatePlan,
  summarizer,
  takeAction,
} from "./nodes/index.js";
import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  isAIMessage,
  isHumanMessage,
  RemoveMessage,
} from "@langchain/core/messages";
import {
  getIssue,
  getIssueComments,
  getMessageContentFromIssue,
} from "../../utils/github/api.js";
import { getGitHubTokensFromConfig } from "../../utils/github-tokens.js";
import { getUntrackedComments } from "../../utils/github/issue-messages.js";
import { initializeSandbox } from "../shared/initialize-sandbox.js";

function takeActionOrGeneratePlan(
  state: PlannerGraphState,
  config: GraphConfig,
): "take-plan-action" | "generate-plan" {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];
  // If the last message is a tool call, and we have executed less than 6 actions, take action.
  // Max actions count is calculated as: maxContextActions * 2 + 1
  // This is because each action generates 2 messages (AI request + tool result) plus 1 initial human message
  const maxContextActions = config.configurable?.maxContextActions ?? 6;
  const maxActionsCount = maxContextActions * 2 + 1;
  if (
    isAIMessage(lastMessage) &&
    lastMessage.tool_calls?.length &&
    messages.length < maxActionsCount
  ) {
    return "take-plan-action";
  }

  // If the last message does not have tool calls, continue to generate plan without modifications.
  return "generate-plan";
}

async function prepareGraphState(
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

  // Remove all messages not marked as summaryMessage, and not human messages.
  const removedNonSummaryMessages = state.messages
    .filter((m) => !m.additional_kwargs?.summaryMessage && !isHumanMessage(m))
    .map((m: BaseMessage) => new RemoveMessage({ id: m.id ?? "" }));
  const summaryMessage = new AIMessage({
    id: uuidv4(),
    content: state.planContextSummary,
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
    planContextSummary: "",
  };

  return new Command({
    update: commandUpdate,
    goto: "initialize-sandbox",
  });
}

const workflow = new StateGraph(PlannerGraphStateObj, GraphConfiguration)
  .addNode("prepare-graph-state", prepareGraphState, {
    ends: [END, "initialize-sandbox"],
  })
  .addNode("initialize-sandbox", initializeSandbox)
  .addNode("generate-plan-context-action", generateAction)
  .addNode("take-plan-action", takeAction)
  .addNode("generate-plan", generatePlan)
  .addNode("summarizer", summarizer)
  .addEdge(START, "prepare-graph-state")
  .addEdge("initialize-sandbox", "generate-plan-context-action")
  .addConditionalEdges(
    "generate-plan-context-action",
    takeActionOrGeneratePlan,
    ["take-plan-action", "generate-plan"],
  )
  .addEdge("take-plan-action", "generate-plan-context-action")
  .addEdge("generate-plan", "summarizer")
  .addEdge("summarizer", END);

export const plannerGraph = workflow.compile();
plannerGraph.name = "Planner";
