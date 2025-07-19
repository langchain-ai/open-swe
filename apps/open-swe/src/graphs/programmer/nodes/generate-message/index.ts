import {
  GraphState,
  GraphConfig,
  GraphUpdate,
  OpenSWETokenData,
} from "@open-swe/shared/open-swe/types";
import {
  loadModel,
  supportsParallelToolCallsParam,
  Task,
} from "../../../../utils/load-model.js";
import {
  createShellTool,
  createApplyPatchTool,
  createRequestHumanHelpToolFields,
  createUpdatePlanToolFields,
  createGetURLContentTool,
} from "../../../../tools/index.js";
import { formatPlanPrompt } from "../../../../utils/plan-prompt.js";
import { stopSandbox } from "../../../../utils/sandbox.js";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getCurrentPlanItem } from "../../../../utils/current-task.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import {
  CODE_REVIEW_PROMPT,
  DEPENDENCIES_INSTALLED_PROMPT,
  DEPENDENCIES_NOT_INSTALLED_PROMPT,
  DYNAMIC_SYSTEM_PROMPT,
  STATIC_SYSTEM_INSTRUCTIONS,
} from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createSearchTool } from "../../../../tools/search.js";
import { createInstallDependenciesTool } from "../../../../tools/install-dependencies.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { getMcpTools } from "../../../../utils/mcp-client.js";
import {
  formatCodeReviewPrompt,
  getCodeReviewFields,
} from "../../../../utils/review.js";
import { filterMessagesWithoutContent } from "../../../../utils/message/content.js";
import {
  AIMessage,
  AIMessageChunk,
  BaseMessage,
  HumanMessage,
  isAIMessage,
  isHumanMessage,
  isToolMessage,
  MessageContent,
  ToolMessage,
} from "@langchain/core/messages";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

interface CacheablePromptSegment {
  type: "text";
  text: string;
  cache_control?: { type: "ephemeral" };
}

interface CacheMetrics {
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  input_tokens: number;
  output_tokens: number;
}

const formatDynamicContextPrompt = (state: GraphState) => {
  return DYNAMIC_SYSTEM_PROMPT.replaceAll(
    "{PLAN_PROMPT_WITH_SUMMARIES}",
    formatPlanPrompt(getActivePlanItems(state.taskPlan), {
      includeSummaries: true,
    }),
  )
    .replaceAll(
      "{PLAN_GENERATION_NOTES}",
      state.contextGatheringNotes || "No context gathering notes available.",
    )
    .replaceAll("{REPO_DIRECTORY}", getRepoAbsolutePath(state.targetRepository))
    .replaceAll(
      "{DEPENDENCIES_INSTALLED_PROMPT}",
      state.dependenciesInstalled
        ? DEPENDENCIES_INSTALLED_PROMPT
        : DEPENDENCIES_NOT_INSTALLED_PROMPT,
    )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    );
};

const formatStaticInstructionsPrompt = (state: GraphState) => {
  return STATIC_SYSTEM_INSTRUCTIONS.replaceAll(
    "{REPO_DIRECTORY}",
    getRepoAbsolutePath(state.targetRepository),
  ).replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules));
};

const formatCacheablePrompt = (state: GraphState): CacheablePromptSegment[] => {
  const codeReview = getCodeReviewFields(state.internalMessages);

  const segments: CacheablePromptSegment[] = [
    // Cache Breakpoint 2: Static Instructions
    {
      type: "text",
      text: formatStaticInstructionsPrompt(state),
      cache_control: { type: "ephemeral" },
    },

    // Cache Breakpoint 3: Dynamic Context
    {
      type: "text",
      text: formatDynamicContextPrompt(state),
      cache_control: { type: "ephemeral" },
    },
  ];

  // Cache Breakpoint 4: Code Review Context (only add if present)
  if (codeReview) {
    segments.push({
      type: "text",
      text: formatCodeReviewPrompt(CODE_REVIEW_PROMPT, {
        review: codeReview.review,
        newActions: codeReview.newActions,
      }),
      cache_control: { type: "ephemeral" },
    });
  }

  return segments.filter((segment) => segment.text.trim() !== "");
};

const calculateCostSavings = (metrics: CacheMetrics): number => {
  const SONNET_4_BASE_RATE = 3.0 / 1_000_000; // $3 per MTok
  const CACHE_WRITE_MULTIPLIER = 1.25;
  const CACHE_READ_MULTIPLIER = 0.1;

  const cacheWriteCost =
    metrics.cache_creation_input_tokens *
    SONNET_4_BASE_RATE *
    CACHE_WRITE_MULTIPLIER;

  const cacheReadCost =
    metrics.cache_read_input_tokens *
    SONNET_4_BASE_RATE *
    CACHE_READ_MULTIPLIER;

  const regularInputCost = metrics.input_tokens * SONNET_4_BASE_RATE;

  // Cost without caching (all tokens at base rate)
  const totalTokens =
    metrics.cache_creation_input_tokens +
    metrics.cache_read_input_tokens +
    metrics.input_tokens;
  const costWithoutCaching = totalTokens * SONNET_4_BASE_RATE;

  // Actual cost with caching
  const actualCost = cacheWriteCost + cacheReadCost + regularInputCost;

  return costWithoutCaching - actualCost;
};

const trackCachePerformance = (response: AIMessageChunk): OpenSWETokenData => {
  const metrics: CacheMetrics = {
    cache_creation_input_tokens:
      response.usage_metadata?.input_token_details?.cache_creation || 0,
    cache_read_input_tokens:
      response.usage_metadata?.input_token_details?.cache_read || 0,
    input_tokens: response.usage_metadata?.input_tokens || 0,
    output_tokens: response.usage_metadata?.output_tokens || 0,
  };

  const totalInputTokens =
    metrics.cache_creation_input_tokens +
    metrics.cache_read_input_tokens +
    metrics.input_tokens;

  const cacheHitRate =
    totalInputTokens > 0
      ? metrics.cache_read_input_tokens / totalInputTokens
      : 0;
  const costSavings = calculateCostSavings(metrics);

  logger.info("Cache Performance", {
    cacheHitRate: `${(cacheHitRate * 100).toFixed(2)}%`,
    costSavings: `$${costSavings.toFixed(4)}`,
    ...metrics,
  });

  return {
    totalInputTokens: metrics.input_tokens,
    totalOutputTokens: metrics.output_tokens,
    totalCacheWrites: metrics.cache_creation_input_tokens,
    totalCacheHits: metrics.cache_read_input_tokens,
  };
};

const addCacheControlToMessageContent = (
  messageContent: MessageContent,
): MessageContent => {
  if (typeof messageContent === "string") {
    return [
      {
        type: "text",
        text: messageContent,
        cache_control: { type: "ephemeral" },
      },
    ];
  } else if (Array.isArray(messageContent)) {
    if ("cache_control" in messageContent[messageContent.length - 1]) {
      // Already set, no-op
      return messageContent;
    }

    const newMessageContent = [...messageContent];
    newMessageContent[newMessageContent.length - 1] = {
      ...newMessageContent[newMessageContent.length - 1],
      cache_control: { type: "ephemeral" },
    };
    return newMessageContent;
  } else {
    logger.warn("Unknown message content type", { messageContent });
    return messageContent;
  }
};

const convertToCacheControlMessage = (message: BaseMessage): BaseMessage => {
  if (isAIMessage(message)) {
    return new AIMessage({
      ...message,
      content: addCacheControlToMessageContent(message.content),
    });
  } else if (isHumanMessage(message)) {
    return new HumanMessage({
      ...message,
      content: addCacheControlToMessageContent(message.content),
    });
  } else if (isToolMessage(message)) {
    return new ToolMessage({
      ...(message as ToolMessage),
      content: addCacheControlToMessageContent(
        (message as ToolMessage).content,
      ),
    });
  } else {
    return message;
  }
};

export async function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.PROGRAMMER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    Task.PROGRAMMER,
  );
  const mcpTools = await getMcpTools(config);

  const tools = [
    createSearchTool(state),
    createShellTool(state),
    createApplyPatchTool(state),
    createRequestHumanHelpToolFields(),
    createUpdatePlanToolFields(),
    createGetURLContentTool(),
    createInstallDependenciesTool(state),
    ...mcpTools,
  ];
  logger.info(
    `MCP tools added to Programmer: ${mcpTools.map((t) => t.name).join(", ")}`,
  );

  // Cache Breakpoint 1: Add cache_control marker to the last tool for tools definition caching
  if (tools.length > 0) {
    tools[tools.length - 1] = {
      ...tools[tools.length - 1],
      cache_control: { type: "ephemeral" },
    } as any;
  }

  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: true,
        }
      : {}),
  });

  const [missingMessages, { taskPlan: latestTaskPlan }] = await Promise.all([
    getMissingMessages(state, config),
    getPlansFromIssue(state, config),
  ]);

  const inputMessages = filterMessagesWithoutContent([
    ...state.internalMessages,
    ...missingMessages,
  ]);
  if (!inputMessages.length) {
    throw new Error("No messages to process.");
  }

  const inputMessagesWithCache = [...inputMessages];
  if (inputMessagesWithCache.length > 0) {
    const lastIndex = inputMessagesWithCache.length - 1;
    inputMessagesWithCache[lastIndex] = convertToCacheControlMessage(
      inputMessagesWithCache[lastIndex],
    );
  }

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatCacheablePrompt({
        ...state,
        taskPlan: latestTaskPlan ?? state.taskPlan,
      }),
    },
    ...inputMessagesWithCache,
  ]);

  // Track cache performance metrics
  const tokenData = trackCachePerformance(response);

  const hasToolCalls = !!response.tool_calls?.length;
  // No tool calls means the graph is going to end. Stop the sandbox.
  let newSandboxSessionId: string | undefined;
  if (!hasToolCalls && state.sandboxSessionId) {
    logger.info("No tool calls found. Stopping sandbox...");
    newSandboxSessionId = await stopSandbox(state.sandboxSessionId);
  }

  logger.info("Generated action", {
    currentTask: getCurrentPlanItem(getActivePlanItems(state.taskPlan)).plan,
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
    ...(response.tool_calls?.map((tc) => ({
      name: tc.name,
      args: tc.args,
    })) || []),
  });

  const newMessagesList = [...missingMessages, response];
  return {
    messages: newMessagesList,
    internalMessages: newMessagesList,
    ...(newSandboxSessionId && { sandboxSessionId: newSandboxSessionId }),
    ...(latestTaskPlan && { taskPlan: latestTaskPlan }),
    tokenData,
  };
}
