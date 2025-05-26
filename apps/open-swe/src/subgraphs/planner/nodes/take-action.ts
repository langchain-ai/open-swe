import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { applyPatchTool, shellTool } from "../../../tools/index.js";
import { GraphConfig } from "../../../types.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";

export async function takeAction(
  state: PlannerGraphState,
  _config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const { plannerMessages: messages } = state;
  const lastMessage = messages[messages.length - 1];

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
  const result: string = await tool.invoke(toolCall.args);
  const toolMessage = new ToolMessage({
    tool_call_id: toolCall.id ?? "",
    content: result,
    name: toolCall.name,
  });

  return {
    plannerMessages: [toolMessage],
  };
}
