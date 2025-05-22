import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { applyPatchTool, shellTool } from "../tools/index.js";
import { GraphState, GraphConfig, GraphUpdate } from "../types.js";

export async function takeAction(
  state: GraphState,
  _config: GraphConfig,
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

  // @ts-expect-error tool.invoke types are weird here...
  const result: ToolMessage = await tool.invoke(toolCall.args);

  return {
    messages: [...state.messages, result],
  };
}
