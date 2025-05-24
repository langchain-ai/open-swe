import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { applyPatchTool, shellTool } from "../tools/index.js";
import { GraphState, GraphConfig, GraphUpdate } from "../types.js";
import {
  checkoutBranchAndCommit,
  getChangedFilesStatus,
  getRepoAbsolutePath,
} from "../utils/git/index.js";
import { Sandbox } from "@e2b/code-interpreter";

export async function takeAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
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
    throw new Error(`Unknown tool: ${toolCall.name}`);
  }
  if (!state.sandboxSessionId) {
    throw new Error(
      "Failed to take action: No sandbox session ID found in state.",
    );
  }

  let result = "";
  try {
    // @ts-expect-error tool.invoke types are weird here...
    result = await tool.invoke(toolCall.args);
  } catch (e) {
    console.error("\nFailed to call tool", e);
    const errMessage = e instanceof Error ? e.message : "Unknown error";
    result = `FAILED TO CALL TOOL: "${toolCall.name}"\n\nError: ${errMessage}`;
  }

  const toolMessage = new ToolMessage({
    tool_call_id: toolCall.id ?? "",
    content: result,
    name: toolCall.name,
  });

  // Always check if there are changed files after running a tool.
  // If there are, commit them.
  const sandbox = await Sandbox.connect(state.sandboxSessionId);
  const changedFiles = await getChangedFilesStatus(
    getRepoAbsolutePath(config),
    sandbox,
  );

  let branchName: string | undefined = state.branchName;
  if (changedFiles.length > 0) {
    console.log(`\nHas ${changedFiles.length} changed files. Committing...`, {
      changedFiles,
    });
    branchName = await checkoutBranchAndCommit(config, sandbox, {
      branchName,
    });
  }

  return {
    messages: [toolMessage],
    ...(branchName && { branchName }),
  };
}
