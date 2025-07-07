import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { createApplyPatchTool, createShellTool } from "../../../tools/index.js";
import { createLangGraphDocsReadTool } from "../../../tools/read-langgraph-docs.js";
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
  formatBadArgsError,
  zodSchemaToString,
} from "../../../utils/zod-to-string.js";
import { Command } from "@langchain/langgraph";
import { truncateOutput } from "../../../utils/truncate-outputs.js";
import { daytonaClient } from "../../../utils/sandbox.js";
import { getCodebaseTree } from "../../../utils/tree.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { shouldDiagnoseError } from "../utils/tool-message-error.js";
import { createInstallDependenciesTool } from "../../../tools/install-dependencies.js";
import { createRgTool } from "../../../tools/rg.js";

const logger = createLogger(LogLevel.INFO, "TakeAction");

export async function takeAction(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const lastMessage = state.internalMessages[state.internalMessages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  if (!state.sandboxSessionId) {
    throw new Error(
      "Failed to take action: No sandbox session ID found in state.",
    );
  }

  const applyPatchTool = createApplyPatchTool(state);
  const shellTool = createShellTool(state);
  const rgTool = createRgTool(state);
  const installDependenciesTool = createInstallDependenciesTool(state);
  const langGraphDocsReadTool = createLangGraphDocsReadTool(config);
  const toolsMap = {
    [applyPatchTool.name]: applyPatchTool,
    [shellTool.name]: shellTool,
    [rgTool.name]: rgTool,
    [installDependenciesTool.name]: installDependenciesTool,
    [langGraphDocsReadTool.name]: langGraphDocsReadTool,
  };

  const toolCalls = lastMessage.tool_calls;
  if (!toolCalls?.length) {
    throw new Error("No tool calls found.");
  }

  const toolCallResultsPromise = toolCalls.map(async (toolCall) => {
    const tool = toolsMap[toolCall.name];

    if (!tool) {
      logger.error(`Unknown tool: ${toolCall.name}`);
      const toolMessage = new ToolMessage({
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
        await tool.invoke(toolCall.args);
      result = toolResult.result;
      toolCallStatus = toolResult.status;
    } catch (e) {
      toolCallStatus = "error";
      if (
        e instanceof Error &&
        e.message === "Received tool input did not match expected schema"
      ) {
        logger.error("Received tool input did not match expected schema", {
          toolCall,
          expectedSchema: zodSchemaToString(tool.schema),
        });
        result = formatBadArgsError(tool.schema, toolCall.args);
      } else {
        logger.error("Failed to call tool", {
          ...(e instanceof Error
            ? { name: e.name, message: e.message, stack: e.stack }
            : { error: e }),
        });
        const errMessage = e instanceof Error ? e.message : "Unknown error";
        result = `FAILED TO CALL TOOL: "${toolCall.name}"\n\nError: ${errMessage}`;
      }
    }

    const toolMessage = new ToolMessage({
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
  const sandbox = await daytonaClient().get(state.sandboxSessionId);
  const changedFiles = await getChangedFilesStatus(
    getRepoAbsolutePath(state.targetRepository),
    sandbox,
  );

  let branchName: string | undefined = state.branchName;
  if (changedFiles.length > 0) {
    logger.info(`Has ${changedFiles.length} changed files. Committing.`, {
      changedFiles,
    });
    branchName = await checkoutBranchAndCommit(
      config,
      state.targetRepository,
      sandbox,
      {
        branchName,
      },
    );
  }

  const shouldRouteDiagnoseNode = shouldDiagnoseError([
    ...state.internalMessages,
    ...toolCallResults,
  ]);

  const codebaseTree = await getCodebaseTree();

  const commandUpdate: GraphUpdate = {
    messages: toolCallResults,
    internalMessages: toolCallResults,
    ...(branchName && { branchName }),
    codebaseTree,
    ...(wereDependenciesInstalled !== null && {
      dependenciesInstalled: wereDependenciesInstalled,
    }),
  };
  return new Command({
    goto: shouldRouteDiagnoseNode ? "diagnose-error" : "progress-plan-step",
    update: commandUpdate,
  });
}
