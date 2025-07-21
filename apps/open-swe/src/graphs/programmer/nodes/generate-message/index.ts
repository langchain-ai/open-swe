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
  CacheablePromptSegment,
  convertToCacheControlMessage,
  trackCachePerformance,
} from "../../../../utils/caching.js";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

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
    tokenData: trackCachePerformance(response),
  };
}
