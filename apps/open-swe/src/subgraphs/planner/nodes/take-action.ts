import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { shellTool } from "../../../tools/index.js";
import { GraphConfig } from "../../../types.js";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { zodSchemaToString } from "../../../utils/zod-to-string.js";
import { formatBadArgsError } from "../../../utils/zod-to-string.js";

const logger = createLogger(LogLevel.INFO, "TakeAction");

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

  let result = "";
  let toolCallStatus: "success" | "error" = "success";
  try {
    const toolResult =
      // @ts-expect-error tool.invoke types are weird here...
      (await tool.invoke(toolCall.args)) as {
        result: string;
        status: "success" | "error";
      };
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
    content: result,
    name: toolCall.name,
    status: toolCallStatus,
  });

  return {
    plannerMessages: [toolMessage],
  };
}
