import {
  isAIMessage,
  isToolMessage,
  ToolMessage,
} from "@langchain/core/messages";
import { createLogger, LogLevel } from "../utils/logger.js";
import { applyPatchTool, shellTool } from "../tools/index.js";
import { GraphState, GraphConfig } from "../types.js";
import {
  checkoutBranchAndCommit,
  getChangedFilesStatus,
  getRepoAbsolutePath,
} from "../utils/git/index.js";
import { Sandbox } from "@e2b/code-interpreter";
import {
  formatBadArgsError,
  zodSchemaToString,
} from "../utils/zod-to-string.js";
import { Command } from "@langchain/langgraph";
import { truncateOutput } from "../utils/truncate-outputs.js";

const logger = createLogger(LogLevel.INFO, "TakeAction");

/**
 * Whether or not to route to the diagnose error step. This is true if:
 * - the last two tool messages are of an error status
 * - two of the last three messages are an error status, including the last tool message
 * @param toolMessages The tool messages to check the status of.
 */
function shouldDiagnoseError(toolMessages: ToolMessage[]) {
  if (
    toolMessages[toolMessages.length - 1].status !== "error" ||
    toolMessages.length < 2
  ) {
    // Last message is not an error, then neither of the below two conditions should be true.
    return false;
  }
  return (
    // Two of the three last tool calls are errors, return true
    // (this is either the last two, or the 3rd, and last since the check above ensures the last is an error)
    toolMessages.slice(-3).filter((m) => m.status === "error").length >= 2
  );
}

export async function takeAction(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const lastMessage = state.messages[state.messages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  const toolsMap = {
    [applyPatchTool.name]: applyPatchTool,
    [shellTool.name]: shellTool,
  };

  const toolCall = lastMessage.tool_calls[0];

  if (!toolCall) {
    throw new Error("No tool call found.");
  }

  const tool = toolsMap[toolCall.name];

  if (!tool) {
    logger.error(`Unknown tool: ${toolCall.name}`);
    return new Command({
      goto: "progress-plan-step",
      update: {
        messages: [
          new ToolMessage({
            tool_call_id: toolCall.id ?? "",
            content: `Unknown tool: ${toolCall.name}`,
            name: toolCall.name,
            status: "error",
          }),
        ],
      },
    });
  }

  if (!state.sandboxSessionId) {
    throw new Error(
      "Failed to take action: No sandbox session ID found in state.",
    );
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

  // Always check if there are changed files after running a tool.
  // If there are, commit them.
  const sandbox = await Sandbox.connect(state.sandboxSessionId);
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

  const shouldRouteDiagnoseNode = shouldDiagnoseError(
    [...state.messages, toolMessage].filter(
      (m): m is ToolMessage =>
        isToolMessage(m) && !m.additional_kwargs?.is_diagnosis,
    ),
  );
  return new Command({
    goto: shouldRouteDiagnoseNode ? "diagnose-error" : "progress-plan-step",
    update: {
      messages: [toolMessage],
      ...(branchName && { branchName }),
    },
  });
}
