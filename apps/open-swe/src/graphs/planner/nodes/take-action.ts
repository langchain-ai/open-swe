import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createShellTool } from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { zodSchemaToString } from "../../../utils/zod-to-string.js";
import { formatBadArgsError } from "../../../utils/zod-to-string.js";
import { truncateOutput } from "../../../utils/truncate-outputs.js";

const logger = createLogger(LogLevel.INFO, "TakeAction");

export async function takeActions(
  state: PlannerGraphState,
  _config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  const shellTool = createShellTool(state);
  const toolsMap = {
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
    return toolMessage;
  });

  const toolCallResults = await Promise.all(toolCallResultsPromise);

  logger.info("Completed planner tool action", {
    ...toolCallResults.map((tc) => ({
      tool_call_id: tc.tool_call_id,
      status: tc.status,
    })),
  });

  return {
    messages: toolCallResults,
  };
}
