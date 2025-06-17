import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { ManagerGraphState, ManagerGraphUpdate } from "../types.js";
import { createIssueTitleAndBodyFromMessages } from "../utils/generate-issue-fields.js";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
} from "@open-swe/shared/constants";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import { createIssue } from "../../../utils/github/api.js";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import { AIMessage, BaseMessage, HumanMessage } from "@langchain/core/messages";
import {
  ISSUE_TITLE_CLOSE_TAG,
  ISSUE_TITLE_OPEN_TAG,
  ISSUE_CONTENT_CLOSE_TAG,
  ISSUE_CONTENT_OPEN_TAG,
} from "../utils/github-issue.js";
import { getBranchName } from "../../../utils/github/git.js";

/**
 * Create new manager session.
 * This node will extract the issue title & body from the conversation history,
 * create a new issue with those fields, then start a new manager session to
 * handle the user's new request/GitHub issue.
 */
export async function createNewSession(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<ManagerGraphUpdate> {
  const titleAndContent = await createIssueTitleAndBodyFromMessages(
    state.messages,
    config,
  );
  const { githubAccessToken } = getGitHubTokensFromConfig(config);
  const newIssue = await createIssue({
    owner: state.targetRepository.owner,
    repo: state.targetRepository.repo,
    title: titleAndContent.title,
    body: titleAndContent.body,
    githubAccessToken,
  });
  if (!newIssue) {
    throw new Error("Failed to create new issue");
  }

  const inputMessages: BaseMessage[] = [
    new HumanMessage({
      id: uuidv4(),
      content: `${ISSUE_TITLE_OPEN_TAG}
  ${titleAndContent.title}
${ISSUE_TITLE_CLOSE_TAG}

${ISSUE_CONTENT_OPEN_TAG}
  ${titleAndContent.body}
${ISSUE_CONTENT_CLOSE_TAG}`,
      additional_kwargs: {
        githubIssueId: newIssue.id,
        isOriginalIssue: true,
      },
    }),
    new AIMessage({
      id: uuidv4(),
      content:
        "I've successfully created a new GitHub issue for your request, and started a planning session for it!",
    }),
  ];

  const langGraphClient = createLangGraphClient({
    defaultHeaders: {
      [GITHUB_TOKEN_COOKIE]: config.configurable?.[GITHUB_TOKEN_COOKIE] ?? "",
      [GITHUB_INSTALLATION_TOKEN_COOKIE]:
        config.configurable?.[GITHUB_INSTALLATION_TOKEN_COOKIE] ?? "",
    },
  });

  const newManagerThreadId = uuidv4();
  const commandUpdate: ManagerGraphUpdate = {
    githubIssueId: newIssue.id,
    targetRepository: state.targetRepository,
    messages: inputMessages,
    branchName: state.branchName ?? getBranchName(config),
  };
  await langGraphClient.runs.create(newManagerThreadId, "manager", {
    input: {},
    command: {
      update: commandUpdate,
      goto: "start-planner",
    },
    config: {
      recursion_limit: 400,
    },
    ifNotExists: "create",
  });

  return {
    messages: [
      new AIMessage({
        id: uuidv4(),
        content: `Success! I just created a new session for your request. Thread ID: ${newManagerThreadId}
      
  TODO: Replace with link to new thread.`,
      }),
    ],
  };
}
