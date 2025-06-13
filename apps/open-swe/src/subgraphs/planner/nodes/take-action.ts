import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createShellTool } from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { PlannerGraphState, PlannerGraphUpdate } from "../types.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { zodSchemaToString } from "../../../utils/zod-to-string.js";
import { formatBadArgsError } from "../../../utils/zod-to-string.js";
import { truncateOutput } from "../../../utils/truncate-outputs.js";

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

  const shellTool = createShellTool(state);
  const toolsMap = {
    [shellTool.name]: shellTool,
  };

  const toolCall = lastMessage.tool_calls[0];

  if (!toolCall) {
    throw new Error("No tool call found.");
  }

  const tool = toolsMap[toolCall.name];

  if (!tool) {
    logger.error(`Unknown tool: ${toolCall.name}`);
    const toolMessage = new ToolMessage({
      tool_call_id: toolCall.id ?? "",
      content: `Unknown tool: ${toolCall.name}`,
      name: toolCall.name,
      status: "error",
    });
    return {
      messages: [toolMessage],
      plannerMessages: [toolMessage],
    };
  }

  logger.info("Executing planner tool action", {
    ...toolCall,
  });

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
    content: truncateOutput(result),
    name: toolCall.name,
    status: toolCallStatus,
  });

  logger.info("Completed planner tool action", {
    tool_call_id: toolCall.id,
    status: toolCallStatus,
  });

  return {
    messages: [toolMessage],
    plannerMessages: [toolMessage],
  };
}
