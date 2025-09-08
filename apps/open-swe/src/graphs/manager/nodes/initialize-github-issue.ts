import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { HumanMessage, isHumanMessage } from "@langchain/core/messages";
import {
  getIssueService,
  getMessageContentFromIssue,
} from "../../../services/issue-service.js";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";

/**
 * The initialize function will do nothing if there's already a human message
 * in the state. If not, it will attempt to get the human message from the GitHub issue.
 */
export async function initializeGithubIssue(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<ManagerGraphUpdate> {
  if (isLocalMode(config)) {
    // In local mode, we don't need GitHub issues
    // The human message should already be in the state from the CLI input
    return {};
  }
  const taskPlan = state.taskPlan;

  if (state.messages.length && state.messages.some(isHumanMessage)) {
    // If there are messages, & at least one is a human message, only attempt to read the updated plan from the issue.
    if (state.githubIssueId) {
      const issueService = getIssueService(config);
      const issue = await issueService.getIssue({
        repo: state.targetRepository,
        issueId: state.githubIssueId,
      });
      if (!issue) {
        throw new Error("Issue not found");
      }
      // no remote issue parsing
    }

    return {
      taskPlan,
    };
  }

  // If there are no messages, ensure there's a GitHub issue to fetch the message from.
  if (!state.githubIssueId) {
    throw new Error("GitHub issue ID not provided");
  }
  if (!state.targetRepository) {
    throw new Error("Target repository not provided");
  }

  const issueService = getIssueService(config);
  const issue = await issueService.getIssue({
    repo: state.targetRepository,
    issueId: state.githubIssueId,
  });
  if (!issue) {
    throw new Error("Issue not found");
  }
  // no remote issue parsing

  const newMessage = new HumanMessage({
    id: uuidv4(),
    content: getMessageContentFromIssue(issue),
    additional_kwargs: {
      githubIssueId: state.githubIssueId,
      isOriginalIssue: true,
    },
  });

  return {
    messages: [newMessage],
    taskPlan,
  };
}
