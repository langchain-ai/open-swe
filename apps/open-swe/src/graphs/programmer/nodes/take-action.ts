import { v4 as uuidv4 } from "uuid";
import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  createApplyPatchTool,
  createGetURLContentTool,
  createShellTool,
} from "../../../tools/index.js";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import {
  checkoutBranchAndCommit,
  getChangedFilesStatus,
} from "../../../utils/github/git.js";
import {
  safeSchemaToString,
  safeBadArgsError,
} from "../../../utils/zod-to-string.js";
import { Command } from "@langchain/langgraph";
import { truncateOutput } from "../../../utils/truncate-outputs.js";
import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import {
  FAILED_TO_GENERATE_TREE_MESSAGE,
  getCodebaseTree,
} from "../../../utils/tree.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { createInstallDependenciesTool } from "../../../tools/install-dependencies.js";
import { createSearchTool } from "../../../tools/search.js";
import { getMcpTools } from "../../../utils/mcp-client.js";
import { shouldDiagnoseError } from "../../../utils/tool-message-error.js";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";

const logger = createLogger(LogLevel.INFO, "TakeAction");

export async function takeAction(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const lastMessage = state.internalMessages[state.internalMessages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  const applyPatchTool = createApplyPatchTool(state);
  const shellTool = createShellTool(state);
  const searchTool = createSearchTool(state);
  const installDependenciesTool = createInstallDependenciesTool(state);
  const getURLContentTool = createGetURLContentTool();

  const mcpTools = await getMcpTools(config);

  const allTools = [
    shellTool,
    searchTool,
    installDependenciesTool,
    applyPatchTool,
    getURLContentTool,
    ...mcpTools,
  ];
  const toolsMap = Object.fromEntries(
    allTools.map((tool) => [tool.name, tool]),
  );

  const toolCalls = lastMessage.tool_calls;
  if (!toolCalls?.length) {
    throw new Error("No tool calls found.");
  }

  const { sandbox, dependenciesInstalled } = await getSandboxWithErrorHandling(
    state.sandboxSessionId,
    state.targetRepository,
    state.branchName,
    config,
  );

  const toolCallResultsPromise = toolCalls.map(async (toolCall) => {
    const tool = toolsMap[toolCall.name];

    if (!tool) {
      logger.error(`Unknown tool: ${toolCall.name}`);
      const toolMessage = new ToolMessage({
        id: uuidv4(),
        tool_call_id: toolCall.id ?? "",
        content: `Unknown tool: ${toolCall.name}`,
        name: toolCall.name,
        status: "error",
      });
      return toolMessage;
    }

    let result = "";
    let toolCallStatus: "success" | "error" = "success";
    try {
      const toolResult: { result: string; status: "success" | "error" } =
        // @ts-expect-error tool.invoke types are weird here...
        await tool.invoke({
          ...toolCall.args,
          // Pass in the existing/new sandbox session ID to the tool call.
          // use `x` prefix to avoid name conflicts with tool args.
          xSandboxSessionId: sandbox.id,
        });
      if (typeof toolResult === "string") {
        result = toolResult;
        toolCallStatus = "success";
      } else {
        result = toolResult.result;
        toolCallStatus = toolResult.status;
      }

      if (!result) {
        result = toolCallStatus;
      }
    } catch (e) {
      toolCallStatus = "error";
      if (
        e instanceof Error &&
        e.message === "Received tool input did not match expected schema"
      ) {
        logger.error("Received tool input did not match expected schema", {
          toolCall,
          expectedSchema: safeSchemaToString(tool.schema),
        });
        result = safeBadArgsError(tool.schema, toolCall.args, toolCall.name);
      } else {
        logger.error("Failed to call tool", {
          ...(e instanceof Error
            ? { name: e.name, message: e.message, stack: e.stack }
            : { error: e }),
        });
        const errMessage = e instanceof Error ? e.message : "Unknown error";
        result = `FAILED TO CALL TOOL: "${toolCall.name}"\n\n${errMessage}`;
      }
    }

    const toolMessage = new ToolMessage({
      id: uuidv4(),
      tool_call_id: toolCall.id ?? "",
      content: truncateOutput(result),
      name: toolCall.name,
      status: toolCallStatus,
    });

    return toolMessage;
  });

  const toolCallResults = await Promise.all(toolCallResultsPromise);

  let wereDependenciesInstalled: boolean | null = null;
  toolCallResults.forEach((toolCallResult) => {
    if (toolCallResult.name === installDependenciesTool.name) {
      wereDependenciesInstalled = toolCallResult.status === "success";
    }
  });

  // Always check if there are changed files after running a tool.
  // If there are, commit them.
  const changedFiles = await getChangedFilesStatus(
    getRepoAbsolutePath(state.targetRepository),
    sandbox,
  );

  let branchName: string | undefined = state.branchName;
  if (changedFiles.length > 0) {
    logger.info(`Has ${changedFiles.length} changed files. Committing.`, {
      changedFiles,
    });
    const { githubInstallationToken } = getGitHubTokensFromConfig(config);
    branchName = await checkoutBranchAndCommit(
      config,
      state.targetRepository,
      sandbox,
      {
        branchName,
        githubInstallationToken,
      },
    );
  }

  const shouldRouteDiagnoseNode = shouldDiagnoseError([
    ...state.internalMessages,
    ...toolCallResults,
  ]);

  const codebaseTree = await getCodebaseTree();
  // If the codebase tree failed to generate, fallback to the previous codebase tree, or if that's not defined, use the failed to generate message.
  const codebaseTreeToReturn =
    codebaseTree === FAILED_TO_GENERATE_TREE_MESSAGE
      ? (state.codebaseTree ?? codebaseTree)
      : codebaseTree;

  // Prioritize wereDependenciesInstalled over dependenciesInstalled
  const dependenciesInstalledUpdate =
    wereDependenciesInstalled !== null
      ? wereDependenciesInstalled
      : dependenciesInstalled !== null
        ? dependenciesInstalled
        : null;

  const commandUpdate: GraphUpdate = {
    messages: toolCallResults,
    internalMessages: toolCallResults,
    ...(branchName && { branchName }),
    codebaseTree: codebaseTreeToReturn,
    sandboxSessionId: sandbox.id,
    ...(dependenciesInstalledUpdate !== null && {
      dependenciesInstalled: dependenciesInstalledUpdate,
    }),
  };
  return new Command({
    goto: shouldRouteDiagnoseNode ? "diagnose-error" : "generate-action",
    update: commandUpdate,
  });
}
