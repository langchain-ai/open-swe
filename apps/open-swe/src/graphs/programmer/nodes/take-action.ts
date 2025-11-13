import { v4 as uuidv4 } from "uuid";
import { isAIMessage, ToolMessage, AIMessage } from "@langchain/core/messages";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import {
  createApplyPatchTool,
  createGetURLContentTool,
  createTextEditorTool,
  createShellTool,
  createSearchDocumentForTool,
  createWriteDefaultTsConfigTool,
} from "../../../tools/index.js";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@openswe/shared/open-swe/types";
import {
  safeSchemaToString,
  safeBadArgsError,
} from "../../../utils/zod-to-string.js";
import { Command } from "@langchain/langgraph";

import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import {
  FAILED_TO_GENERATE_TREE_MESSAGE,
  FeatureTreeOptions,
  getCodebaseTree,
} from "../../../utils/tree.js";
import { createInstallDependenciesTool } from "../../../tools/install-dependencies.js";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { createGrepTool } from "../../../tools/grep.js";
import { getMcpTools } from "../../../utils/mcp-client.js";
import { shouldDiagnoseError } from "../../../utils/tool-message-error.js";
import { processToolCallContent } from "../../../utils/tool-output-processing.js";
import { filterUnsafeCommands } from "../../../utils/command-evaluation.js";
import { getActiveTask } from "@openswe/shared/open-swe/tasks";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";

const logger = createLogger(LogLevel.INFO, "TakeAction");

export async function takeAction(
  state: GraphState,
  config: GraphConfig,
): Promise<Command> {
  const lastMessage = state.internalMessages[state.internalMessages.length - 1];

  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }

  const applyPatchTool = createApplyPatchTool(state, config);
  const shellTool = createShellTool(state, config);
  const searchTool = createGrepTool(state, config);
  const textEditorTool = createTextEditorTool(state, config);
  const installDependenciesTool = createInstallDependenciesTool(state, config);
  const getURLContentTool = createGetURLContentTool(state);
  const searchDocumentForTool = createSearchDocumentForTool(state, config);
  const mcpTools = await getMcpTools(config);
  const writeDefaultTsConfigTool = createWriteDefaultTsConfigTool(
    state,
    config,
  );

  const higherContextLimitToolNames = [
    ...mcpTools.map((t) => t.name),
    getURLContentTool.name,
    searchDocumentForTool.name,
    writeDefaultTsConfigTool.name,
  ];

  const allTools = [
    shellTool,
    searchTool,
    textEditorTool,
    installDependenciesTool,
    applyPatchTool,
    getURLContentTool,
    searchDocumentForTool,
    writeDefaultTsConfigTool,
    ...mcpTools,
  ];
  const toolsMap = Object.fromEntries(
    allTools.map((tool) => [tool.name, tool]),
  );

  let toolCalls = lastMessage.tool_calls;
  if (!toolCalls?.length) {
    throw new Error("No tool calls found.");
  }

  // Filter out unsafe commands only in local mode
  let modifiedMessage: AIMessage | undefined;
  let wasFiltered = false;
  if (isLocalMode(config)) {
    const filterResult = await filterUnsafeCommands(toolCalls, config);

    if (filterResult.wasFiltered) {
      wasFiltered = true;
      modifiedMessage = new AIMessage({
        ...lastMessage,
        tool_calls: filterResult.filteredToolCalls,
      });
      toolCalls = filterResult.filteredToolCalls;
    }
  }

  const { sandbox, dependenciesInstalled } = await getSandboxWithErrorHandling(
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
        id: uuidv4(),
        tool_call_id: toolCall.id ?? "",
        content: `Unknown tool: ${toolCall.name}`,
        name: toolCall.name,
        status: "error",
      });
      return { toolMessage, stateUpdates: undefined };
    }

    let result = "";
    let toolCallStatus: "success" | "error" = "success";
    try {
      const toolResult: { result: string; status: "success" | "error" } =
        // @ts-expect-error tool.invoke types are weird here...
        await tool.invoke({
          ...toolCall.args,
          // Only pass sandbox session ID in sandbox mode, not local mode
          ...(isLocalMode(config) ? {} : { xSandboxSessionId: sandbox.id }),
        });
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
  const toolCallResults = toolCallResultsWithUpdates.map(
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

  let wereDependenciesInstalled: boolean | null = null;
  toolCallResults.forEach((toolCallResult) => {
    if (toolCallResult.name === installDependenciesTool.name) {
      wereDependenciesInstalled = toolCallResult.status === "success";
    }
  });

  const shouldRouteDiagnoseNode = shouldDiagnoseError([
    ...state.internalMessages,
    ...toolCallResults,
  ]);

  const featureLookup = new Map<string, FeatureNode>();
  const combinedFeatures: FeatureNode[] = [];
  for (const feature of [
    ...(state.features ?? []),
    ...(state.featureDependencies ?? []),
  ]) {
    if (!feature?.id) continue;
    if (!featureLookup.has(feature.id)) {
      featureLookup.set(feature.id, feature);
      combinedFeatures.push(feature);
    }
  }

  const aggregatedFeatureIds = new Set<string>();
  for (const featureId of state.activeFeatureIds ?? []) {
    const trimmed = featureId.trim();
    if (trimmed) {
      aggregatedFeatureIds.add(trimmed);
    }
  }

  if (state.taskPlan) {
    try {
      const activeTask = getActiveTask(state.taskPlan);
      for (const featureId of activeTask.featureIds ?? []) {
        const trimmed = featureId.trim();
        if (trimmed) {
          aggregatedFeatureIds.add(trimmed);
        }
      }
      const activeRevision = activeTask.planRevisions[activeTask.activeRevisionIndex];
      if (activeRevision) {
        for (const item of activeRevision.plans) {
          for (const featureId of item.featureIds ?? []) {
            const trimmed = featureId.trim();
            if (trimmed) {
              aggregatedFeatureIds.add(trimmed);
            }
          }
        }
      }
    } catch (error) {
      logger.debug("Unable to resolve active task for feature annotations", {
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const featureIds = aggregatedFeatureIds.size
    ? Array.from(aggregatedFeatureIds)
    : combinedFeatures
        .map((feature) => feature?.id?.trim())
        .filter((id): id is string => Boolean(id));

  const featureTreeOptions: FeatureTreeOptions | undefined =
    combinedFeatures.length > 0 && featureIds.length > 0
      ? {
          features: combinedFeatures,
          featureIds,
          mode: "annotate",
        }
      : undefined;

  const codebaseTree = await getCodebaseTree(
    config,
    undefined,
    undefined,
    featureTreeOptions,
  );
  // If the codebase tree failed to generate, fallback to the previous codebase tree, or if that's not defined, use the failed to generate message.
  const codebaseTreeToReturn =
    codebaseTree === FAILED_TO_GENERATE_TREE_MESSAGE
      ? (state.codebaseTree ?? codebaseTree)
      : codebaseTree;

  // Prioritize wereDependenciesInstalled over dependenciesInstalled
  const dependenciesInstalledUpdate =
    wereDependenciesInstalled !== null
      ? wereDependenciesInstalled
      : dependenciesInstalled !== null
        ? dependenciesInstalled
        : null;

  // Include the modified message if it was filtered
  const internalMessagesUpdate =
    wasFiltered && modifiedMessage
      ? [modifiedMessage, ...toolCallResults]
      : toolCallResults;

  const commandUpdate: GraphUpdate = {
    messages: toolCallResults,
    internalMessages: internalMessagesUpdate,
    codebaseTree: codebaseTreeToReturn,
    sandboxSessionId: sandbox.id,
    ...(dependenciesInstalledUpdate !== null && {
      dependenciesInstalled: dependenciesInstalledUpdate,
    }),
    ...allStateUpdates,
  };
  return new Command({
    goto: shouldRouteDiagnoseNode ? "diagnose-error" : "generate-action",
    update: commandUpdate,
  });
}
