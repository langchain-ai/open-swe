import { v4 as uuidv4 } from "uuid";
import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import {
  GraphConfig,
  GraphState,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import { HumanInterrupt, HumanResponse } from "@langchain/langgraph/prebuilt";
import { END, interrupt, Command } from "@langchain/langgraph";
import { GITHUB_USER_LOGIN_HEADER } from "@open-swe/shared/constants";
import {
  getSandboxWithErrorHandling,
  stopSandbox,
} from "../../../utils/sandbox.js";
import { postGitHubIssueComment } from "../../planner/nodes/proposed-plan.js";
import { getOpenSweAppUrl } from "../../../utils/url-helpers.js";

const constructDescription = (helpRequest: string): string => {
  return `The agent has requested help. Here is the help request:
  
\`\`\`
${helpRequest}
\`\`\``;
};

export async function requestHelp(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const lastMessage = state.internalMessages[state.internalMessages.length - 1];
  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }
  const sandboxSessionId = state.sandboxSessionId;
  if (sandboxSessionId) {
    await stopSandbox(sandboxSessionId);
  }

  const toolCall = lastMessage.tool_calls[0];

  const threadId = config.configurable?.thread_id;
  if (!threadId) {
    throw new Error("Thread ID not found in config");
  }

  const userLogin = config.configurable?.[GITHUB_USER_LOGIN_HEADER];
  const userTag = userLogin ? `@${userLogin} ` : "";

  const runUrl = getOpenSweAppUrl(threadId);
  const commentBody = runUrl
    ? `### 🤖 Open SWE Needs Help

${userTag}I've encountered a situation where I need human assistance to continue.

**Help Request:**
${toolCall.args.help_request}

You can view and respond to this request in the [Open SWE interface](${runUrl}).

Please provide guidance so I can continue working on this issue.`
    : `### 🤖 Open SWE Needs Help

${userTag}I've encountered a situation where I need human assistance to continue.

**Help Request:**
${toolCall.args.help_request}

Please check the Open SWE interface to respond to this request.`;

  await postGitHubIssueComment({
    githubIssueId: state.githubIssueId,
    targetRepository: state.targetRepository,
    commentBody,
    config,
  });

  const interruptInput: HumanInterrupt = {
    action_request: {
      action: "Help Requested",
      args: {},
    },
    config: {
      allow_accept: false,
      allow_edit: false,
      allow_ignore: true,
      allow_respond: true,
    },
    description: constructDescription(toolCall.args.help_request),
  };
  const interruptRes = interrupt<HumanInterrupt[], HumanResponse[]>([
    interruptInput,
  ])[0];

  if (interruptRes.type === "ignore") {
    return new Command({
      goto: END,
    });
  }

  if (interruptRes.type === "response") {
    if (typeof interruptRes.args !== "string") {
      throw new Error("Interrupt response expected to be a string.");
    }

    const { sandbox, codebaseTree, dependenciesInstalled } =
      await getSandboxWithErrorHandling(
        state.sandboxSessionId,
        state.targetRepository,
        state.branchName,
        config,
      );

    const toolMessage = new ToolMessage({
      id: uuidv4(),
      tool_call_id: toolCall.id ?? "",
      content: `Human response: ${interruptRes.args}`,
      status: "success",
    });

    const commandUpdate: GraphUpdate = {
      messages: [toolMessage],
      internalMessages: [toolMessage],
      sandboxSessionId: sandbox.id,
      ...(codebaseTree && { codebaseTree }),
      ...(dependenciesInstalled !== null && { dependenciesInstalled }),
    };
    return new Command({
      goto: "generate-action",
      update: commandUpdate,
    });
  }

  throw new Error(
    `Invalid interrupt response type. Must be one of 'ignore' or 'response'. Received: ${interruptRes.type}`,
  );
}
