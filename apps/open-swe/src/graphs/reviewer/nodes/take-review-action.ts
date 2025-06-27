import { isAIMessage, ToolMessage } from "@langchain/core/messages";
import { createShellTool } from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@open-swe/shared/open-swe/reviewer/types";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { zodSchemaToString } from "../../../utils/zod-to-string.js";
import { formatBadArgsError } from "../../../utils/zod-to-string.js";
import { truncateOutput } from "../../../utils/truncate-outputs.js";
import { createRgTool } from "../../../tools/rg.js";
import {
  getChangedFilesStatus,
  stashAndClearChanges,
} from "../../../utils/github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { daytonaClient } from "../../../utils/sandbox.js";

const logger = createLogger(LogLevel.INFO, "TakeReviewAction");

export async function takeReviewerActions(
  state: ReviewerGraphState,
  _config: GraphConfig,
): Promise<ReviewerGraphUpdate> {
  const { reviewerMessages } = state;
  const lastMessage = reviewerMessages[reviewerMessages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  const shellTool = createShellTool(state);
  const rgTool = createRgTool(state);
  const toolsMap = {
    [shellTool.name]: shellTool,
    [rgTool.name]: rgTool,
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

    logger.info("Executing review action", {
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

  logger.info("Completed review action", {
    ...toolCallResults.map((tc) => ({
      tool_call_id: tc.tool_call_id,
      status: tc.status,
    })),
  });

  return {
    messages: toolCallResults,
    reviewerMessages: toolCallResults,
  };
}
