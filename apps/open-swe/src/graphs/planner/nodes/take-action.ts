import { v4 as uuidv4 } from "uuid";
import {
  isAIMessage,
  isToolMessage,
  ToolMessage,
} from "@langchain/core/messages";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import {
  createGetURLContentTool,
  createShellTool,
  createSearchDocumentForTool,
} from "../../../tools/index.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  safeSchemaToString,
  safeBadArgsError,
} from "../../../utils/zod-to-string.js";

import { createGrepTool } from "../../../tools/grep.js";
import {
  getChangedFilesStatus,
  stashAndClearChanges,
} from "../../../utils/github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { createScratchpadTool } from "../../../tools/scratchpad.js";
import { getMcpTools } from "../../../utils/mcp-client.js";
import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import { shouldDiagnoseError } from "../../../utils/tool-message-error.js";
import { Command } from "@langchain/langgraph";
import { filterHiddenMessages } from "../../../utils/message/filter-hidden.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import { processToolCallContent } from "../../../utils/tool-output-processing.js";
import { createViewTool } from "../../../tools/builtin-tools/view.js";

const logger = createLogger(LogLevel.INFO, "TakeAction");

export async function takeActions(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const { messages } = state;
  const lastMessage = messages[messages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  const viewTool = createViewTool(state, config);
  const shellTool = createShellTool(state, config);
  const searchTool = createGrepTool(state, config);
  const scratchpadTool = createScratchpadTool("");
  const getURLContentTool = createGetURLContentTool(state);
  const searchDocumentForTool = createSearchDocumentForTool(state, config);
  const mcpTools = await getMcpTools(config);

  const higherContextLimitToolNames = [
    ...mcpTools.map((t) => t.name),
    getURLContentTool.name,
    searchDocumentForTool.name,
  ];

  const allTools = [
    viewTool,
    shellTool,
    searchTool,
    scratchpadTool,
    getURLContentTool,
    searchDocumentForTool,
    ...mcpTools,
  ];
  const toolsMap = Object.fromEntries(
    allTools.map((tool) => [tool.name, tool]),
  );

  const toolCalls = lastMessage.tool_calls;
  if (!toolCalls?.length) {
    throw new Error("No tool calls found.");
  }

  const { sandbox, codebaseTree, dependenciesInstalled } =
    await getSandboxWithErrorHandling(
      state.sandboxSessionId,
      state.targetRepository,
      state.branchName,
      config,
    );

  const toolCallResultsPromise = toolCalls.map(async (toolCall) => {
    const tool = toolsMap[toolCall.name];
    if (!tool) {
      logger.error(`Unknown tool: ${toolCall.name}`);
      const toolMessage = new ToolMessage({
        id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
        tool_call_id: toolCall.id ?? "",
        content: `Unknown tool: ${toolCall.name}`,
        name: toolCall.name,
        status: "error",
      });

      return { toolMessage, stateUpdates: undefined };
    }

    logger.info("Executing planner tool action", {
      ...toolCall,
    });

    let result = "";
    let toolCallStatus: "success" | "error" = "success";
    try {
      const toolResult =
        // @ts-expect-error tool.invoke types are weird here...
        (await tool.invoke({
          ...toolCall.args,
          // Only pass sandbox session ID in sandbox mode, not local mode
          ...(isLocalMode(config) ? {} : { xSandboxSessionId: sandbox.id }),
        })) as {
          result: string;
          status: "success" | "error";
        };
      if (typeof toolResult === "string") {
        result = toolResult;
        toolCallStatus = "success";
      } else {
        result = toolResult.result;
        toolCallStatus = toolResult.status;
      }

      if (!result) {
        result =
          toolCallStatus === "success"
            ? "Tool call returned no result"
            : "Tool call failed";
      }
    } catch (e) {
      toolCallStatus = "error";
      if (
        e instanceof Error &&
        e.message === "Received tool input did not match expected schema"
      ) {
        logger.error("Received tool input did not match expected schema", {
          toolCall,
          expectedSchema: safeSchemaToString(tool.schema),
        });
        result = safeBadArgsError(tool.schema, toolCall.args, toolCall.name);
      } else {
        logger.error("Failed to call tool", {
          ...(e instanceof Error
            ? { name: e.name, message: e.message, stack: e.stack }
            : { error: e }),
        });
        const errMessage = e instanceof Error ? e.message : "Unknown error";
        result = `FAILED TO CALL TOOL: "${toolCall.name}"\n\n${errMessage}`;
      }
    }

    const { content, stateUpdates } = await processToolCallContent(
      toolCall,
      result,
      {
        higherContextLimitToolNames,
        state,
        config,
      },
    );

    const toolMessage = new ToolMessage({
      id: uuidv4(),
      tool_call_id: toolCall.id ?? "",
      content,
      name: toolCall.name,
      status: toolCallStatus,
    });

    return { toolMessage, stateUpdates };
  });

  const toolCallResultsWithUpdates = await Promise.all(toolCallResultsPromise);
  let toolCallResults = toolCallResultsWithUpdates.map(
    (item) => item.toolMessage,
  );

  // merging document cache updates from tool calls
  const allStateUpdates = toolCallResultsWithUpdates
    .map((item) => item.stateUpdates)
    .filter(Boolean)
    .reduce(
      (acc: { documentCache: Record<string, string> }, update) => {
        if (update?.documentCache) {
          acc.documentCache = { ...acc.documentCache, ...update.documentCache };
        }
        return acc;
      },
      { documentCache: {} } as { documentCache: Record<string, string> },
    );

  if (!isLocalMode(config)) {
    const repoPath = isLocalMode(config)
      ? getLocalWorkingDirectory()
      : getRepoAbsolutePath(state.targetRepository);
    const changedFiles = await getChangedFilesStatus(repoPath, sandbox, config);
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
  }

  logger.info("Completed planner tool action", {
    ...toolCallResults.map((tc) => ({
      tool_call_id: tc.tool_call_id,
      status: tc.status,
    })),
  });

  const commandUpdate: PlannerGraphUpdate = {
    messages: toolCallResults,
    sandboxSessionId: sandbox.id,
    ...(codebaseTree && { codebaseTree }),
    ...(dependenciesInstalled !== null && { dependenciesInstalled }),
    ...allStateUpdates,
  };

  const maxContextActions = config.configurable?.maxContextActions ?? 75;
  const maxActionsCount = maxContextActions * 2;
  // Exclude hidden messages, and messages that are not AI messages or tool messages.
  const filteredMessages = filterHiddenMessages([
    ...state.messages,
    ...(commandUpdate.messages ?? []),
  ]).filter((m) => isAIMessage(m) || isToolMessage(m));
  if (filteredMessages.length >= maxActionsCount) {
    // If we've exceeded the max actions count, we should generate a plan.
    logger.info("Exceeded max actions count, generating plan.", {
      maxActionsCount,
      filteredMessages,
    });
    return new Command({
      goto: "generate-plan",
      update: commandUpdate,
    });
  }

  const shouldRouteDiagnoseNode = shouldDiagnoseError([
    ...state.messages,
    ...toolCallResults,
  ]);

  return new Command({
    goto: shouldRouteDiagnoseNode
      ? "diagnose-error"
      : "generate-plan-context-action",
    update: commandUpdate,
  });
}
