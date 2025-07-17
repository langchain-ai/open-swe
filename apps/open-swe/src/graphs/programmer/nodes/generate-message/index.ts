import {
  GraphState,
  GraphConfig,
  GraphUpdate,
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
  INSTALL_DEPENDENCIES_TOOL_PROMPT,
  SYSTEM_PROMPT,
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

const formatCacheablePrompt = (state: GraphState): CacheablePromptSegment[] => {
  const repoDirectory = getRepoAbsolutePath(state.targetRepository);
  const activePlanItems = getActivePlanItems(state.taskPlan);
  const codeReview = getCodeReviewFields(state.internalMessages);

  const segments: CacheablePromptSegment[] = [
    // Cache Breakpoint 2: Static Instructions
    {
      type: "text",
      text: STATIC_SYSTEM_INSTRUCTIONS,
      cache_control: { type: "ephemeral" },
    },

    // Cache Breakpoint 3: Dynamic Context
    {
      type: "text",
      text: `# Context

<plan_information>
## Generated Plan with Summaries
${formatPlanPrompt(activePlanItems, { includeSummaries: true })}

## Plan Generation Notes
These are notes you took while gathering context for the plan:
<plan-generation-notes>
${state.contextGatheringNotes || "No context gathering notes available."}
</plan-generation-notes>

## Current Task Statuses
${formatPlanPrompt(activePlanItems)}
</plan_information>

<codebase_structure>
## Codebase Tree (3 levels deep, respecting .gitignore)
Generated via: \`git ls-files | tree --fromfile -L 3\`
Location: ${repoDirectory}

${state.codebaseTree || "No codebase tree generated yet."}
</codebase_structure>

${formatCustomRulesPrompt(state.customRules)}`,
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

const trackCachePerformance = (response: any) => {
  const metrics: CacheMetrics = {
    cache_creation_input_tokens:
      response.usage?.cache_creation_input_tokens || 0,
    cache_read_input_tokens: response.usage?.cache_read_input_tokens || 0,
    input_tokens: response.usage?.input_tokens || 0,
    output_tokens: response.usage?.output_tokens || 0,
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
    ...mcpTools,
    // Only provide the dependencies installed tool if they're not already installed.
    ...(state.dependenciesInstalled
      ? []
      : [createInstallDependenciesTool(state)]),
  ];
  logger.info(
    `MCP tools added to Programmer: ${mcpTools.map((t) => t.name).join(", ")}`,
  );


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

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatCacheablePrompt({
        ...state,
        taskPlan: latestTaskPlan ?? state.taskPlan,
      }),
    },
    ...inputMessages,
  ]);

  // Track cache performance metrics
  trackCachePerformance(response);

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
  };
}
