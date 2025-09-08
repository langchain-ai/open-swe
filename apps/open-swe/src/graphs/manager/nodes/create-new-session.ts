import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { createIssueFieldsFromMessages } from "../utils/generate-issue-fields.js";
import {
  LOCAL_MODE_HEADER,
  MANAGER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
} from "@openswe/shared/constants";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import { getIssueService } from "../../../services/issue-service.js";
import { AIMessage, BaseMessage, HumanMessage } from "@langchain/core/messages";
import {
  ISSUE_TITLE_CLOSE_TAG,
  ISSUE_TITLE_OPEN_TAG,
  ISSUE_CONTENT_CLOSE_TAG,
  ISSUE_CONTENT_OPEN_TAG,
  formatContentForIssueBody,
} from "../../../utils/issue-messages.js";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import { StreamMode } from "@langchain/langgraph-sdk";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { shouldCreateIssue } from "../../../utils/should-create-issue.js";

/**
 * Create new manager session.
 * This node will extract the issue title & body from the conversation history,
 * create a new issue with those fields, then start a new manager session to
 * handle the user's new request/issue.
 */
export async function createNewSession(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<ManagerGraphUpdate> {
  const titleAndContent = await createIssueFieldsFromMessages(
    state.messages,
    config.configurable,
  );

  let newIssueNumber: number | undefined;
  if (shouldCreateIssue(config)) {
    const issueService = getIssueService(config);
    const newIssue = await issueService.createIssue({
      repo: state.targetRepository,
      title: titleAndContent.title,
      body: formatContentForIssueBody(titleAndContent.body),
    });
    if (!newIssue) {
      throw new Error("Failed to create new issue");
    }
    newIssueNumber = Number(newIssue.id);
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
        githubIssueId: newIssueNumber,
        isOriginalIssue: true,
      },
    }),
    new AIMessage({
      id: uuidv4(),
      content:
        "I've created a new issue for your request and started a planning session for it!",
    }),
  ];

  const isLocal = isLocalMode(config);
  const defaultHeaders: Record<string, string> = isLocal
    ? { [LOCAL_MODE_HEADER]: "true" }
    : {};

  const langGraphClient = createLangGraphClient({
    defaultHeaders,
  });

  const newManagerThreadId = uuidv4();
  const commandUpdate: ManagerGraphUpdate = {
    githubIssueId: newIssueNumber,
    targetRepository: state.targetRepository,
    messages: inputMessages,
    branchName: state.branchName ?? "",
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
