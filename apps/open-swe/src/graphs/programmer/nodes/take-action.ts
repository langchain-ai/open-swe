import {
  isAIMessage,
  isToolMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { createApplyPatchTool, createShellTool } from "../../../tools/index.js";
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

const logger = createLogger(LogLevel.INFO, "TakeAction");

/**
 * Group tool messages by their parent AI message
 * @param messages Array of messages to process
 * @returns Array of tool message groups, where each group contains tool messages tied to the same AI message
 */
function groupToolMessagesByAIMessage(messages: Array<any>): ToolMessage[][] {
  const groups: ToolMessage[][] = [];
  let currentGroup: ToolMessage[] = [];
  let processingToolsForAI = false;

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];

    if (isAIMessage(message)) {
      // If we were already processing tools for a previous AI message, save that group
      if (currentGroup.length > 0) {
        groups.push([...currentGroup]);
        currentGroup = [];
      }
      processingToolsForAI = true;
    } else if (
      isToolMessage(message) &&
      processingToolsForAI &&
      !message.additional_kwargs?.is_diagnosis
    ) {
      currentGroup.push(message);
    } else if (!isToolMessage(message) && processingToolsForAI) {
      // We've encountered a non-tool message after an AI message, end the current group
      if (currentGroup.length > 0) {
        groups.push([...currentGroup]);
        currentGroup = [];
      }
      processingToolsForAI = false;
    }
  }

  // Add the last group if it exists
  if (currentGroup.length > 0) {
    groups.push(currentGroup);
  }

  return groups;
}

/**
 * Calculate the error rate for a group of tool messages
 * @param group Array of tool messages
 * @returns Error rate as a number between 0 and 1
 */
function calculateErrorRate(group: ToolMessage[]): number {
  if (group.length === 0) return 0;
  const errorCount = group.filter((m) => m.status === "error").length;
  return errorCount / group.length;
}

/**
 * Whether or not to route to the diagnose error step. This is true if:
 * - the last three tool call groups all have >= 75% error rates
 * @param messages All messages to analyze
 */
function shouldDiagnoseError(messages: Array<any>) {
  // Group tool messages by their parent AI message
  const toolGroups = groupToolMessagesByAIMessage(messages);

  // If we don't have at least 3 groups, we can't make a determination
  if (toolGroups.length < 3) return false;

  // Get the last three groups
  const lastThreeGroups = toolGroups.slice(-3);

  // Check if all of the last three groups have an error rate >= 75%
  const ERROR_THRESHOLD = 0.75; // 75%
  return lastThreeGroups.every(
    (group) => calculateErrorRate(group) >= ERROR_THRESHOLD,
  );
}

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
  const toolsMap = {
    [applyPatchTool.name]: applyPatchTool,
    [shellTool.name]: shellTool,
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
  };
  return new Command({
    goto: shouldRouteDiagnoseNode ? "diagnose-error" : "progress-plan-step",
    update: commandUpdate,
  });
}
