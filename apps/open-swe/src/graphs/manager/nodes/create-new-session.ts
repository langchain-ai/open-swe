import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@open-swe/shared/open-swe/manager/types";
import { createIssueFieldsFromMessages } from "../utils/generate-issue-fields.js";
import {
  GITHUB_INSTALLATION_ID,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_PAT,
  LOCAL_MODE_HEADER,
  MANAGER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
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
  formatContentForIssueBody,
} from "../../../utils/github/issue-messages.js";
import { getBranchName } from "../../../utils/github/git.js";
import { getDefaultHeaders } from "../../../utils/default-headers.js";
import { getCustomConfigurableFields } from "../../../utils/config.js";
import { StreamMode } from "@langchain/langgraph-sdk";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { regenerateInstallationToken } from "../../../utils/github/regenerate-token.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "CreateNewSession");

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
  const titleAndContent = await createIssueFieldsFromMessages(
    state.messages,
    config.configurable,
  );
  const { githubAccessToken } = getGitHubTokensFromConfig(config);
  const newIssue = await createIssue({
    owner: state.targetRepository.owner,
    repo: state.targetRepository.repo,
    title: titleAndContent.title,
    body: formatContentForIssueBody(titleAndContent.body),
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
        githubIssueId: newIssue.number,
        isOriginalIssue: true,
      },
    }),
    new AIMessage({
      id: uuidv4(),
      content:
        "I've successfully created a new GitHub issue for your request, and started a planning session for it!",
    }),
  ];

  const isLocal = isLocalMode(config);
  const defaultHeaders = isLocal
    ? { [LOCAL_MODE_HEADER]: "true" }
    : getDefaultHeaders(config);

  // Only regenerate if its not running in local mode, and the GITHUB_PAT is not in the headers
  // If the GITHUB_PAT is in the headers, then it means we're running an eval and this does not need to be regenerated
  if (!isLocal && !(GITHUB_PAT in defaultHeaders)) {
    logger.info("Regenerating installation token before starting new session.");
    defaultHeaders[GITHUB_INSTALLATION_TOKEN_COOKIE] =
      await regenerateInstallationToken(defaultHeaders[GITHUB_INSTALLATION_ID]);
    logger.info("Regenerated installation token before starting new session.");
  }

  const langGraphClient = createLangGraphClient({
    defaultHeaders,
  });

  const newManagerThreadId = uuidv4();
  const commandUpdate: ManagerGraphUpdate = {
    githubIssueId: newIssue.number,
    targetRepository: state.targetRepository,
    messages: inputMessages,
    branchName: state.branchName ?? getBranchName(config),
  };
  await langGraphClient.runs.create(newManagerThreadId, MANAGER_GRAPH_ID, {
    input: {},
    command: {
      update: commandUpdate,
      goto: "start-planner",
    },
    config: {
      recursion_limit: 400,
      configurable: getCustomConfigurableFields(config),
    },
    ifNotExists: "create",
    streamResumable: true,
    streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
  });

  return {
    messages: [
      new AIMessage({
        id: uuidv4(),
        content: `Success! I just created a new session for your request. Thread ID: \`${newManagerThreadId}\`

Click [here](/chat/${newManagerThreadId}) to view the thread.`,
      }),
    ],
  };
}
