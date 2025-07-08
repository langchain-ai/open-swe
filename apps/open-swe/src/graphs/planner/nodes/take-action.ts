import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import {
  createGetURLContentTool,
  createShellTool,
} from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { zodSchemaToString } from "../../../utils/zod-to-string.js";
import { formatBadArgsError } from "../../../utils/zod-to-string.js";
import { truncateOutput } from "../../../utils/truncate-outputs.js";
import { createRgTool } from "../../../tools/rg.js";
import {
  getChangedFilesStatus,
  stashAndClearChanges,
} from "../../../utils/github/git.js";
import { createFindInstancesOfTool } from "../../../tools/find-instances-of.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { daytonaClient } from "../../../utils/sandbox.js";
import { createPlannerNotesTool } from "../../../tools/planner-notes.js";

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
  const rgTool = createRgTool(state);
  const plannerNotesTool = createPlannerNotesTool();
  const findInstancesOfTool = createFindInstancesOfTool(state);
  const getURLContentTool = createGetURLContentTool();
  const toolsMap = {
    [shellTool.name]: shellTool,
    [rgTool.name]: rgTool,
    [plannerNotesTool.name]: plannerNotesTool,
    [findInstancesOfTool.name]: findInstancesOfTool,
    [getURLContentTool.name]: getURLContentTool,
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

    const truncatedOutput =
      toolCall.name === getURLContentTool.name
        ? // Allow for more context to be included from URL contents.
          truncateOutput(result, {
            numStartCharacters: 10000,
            numEndCharacters: 10000,
          })
        : truncateOutput(result);

    const toolMessage = new ToolMessage({
      tool_call_id: toolCall.id ?? "",
      content: truncatedOutput,
      name: toolCall.name,
      status: toolCallStatus,
    });
    return toolMessage;
  });

  let toolCallResults = await Promise.all(toolCallResultsPromise);
  const sandbox = await daytonaClient().get(state.sandboxSessionId);
  const repoPath = getRepoAbsolutePath(state.targetRepository);
  const changedFiles = await getChangedFilesStatus(repoPath, sandbox);
  if (changedFiles?.length > 0) {
    logger.warn(
      "Changes found in the codebase after taking action. Reverting.",
      {
        changedFiles,
      },
    );
    await stashAndClearChanges(repoPath, sandbox);

    // Rewrite the tool call contents to include a changed files warning.
    toolCallResults = toolCallResults.map(
      (tc) =>
        new ToolMessage({
          ...tc,
          content: `**WARNING**: THIS TOOL, OR A PREVIOUS TOOL HAS CHANGED FILES IN THE REPO.
Remember that you are only permitted to take **READ** actions during the planning step. The changes have been reverted.

Please ensure you only take read actions during the planning step to gather context. You may also call the \`take_notes\` tool at any time to record important information for the programmer step.

Command Output:\n
${tc.content}`,
        }),
    );
  }

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
