import { v4 as uuidv4 } from "uuid";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import {
  loadModel,
  supportsParallelToolCallsParam,
  Task,
} from "../../../../utils/llms/index.js";
import {
  createShellTool,
  createApplyPatchTool,
  createRequestHumanHelpToolFields,
  createUpdatePlanToolFields,
  createGetURLContentTool,
  createSearchDocumentForTool,
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
  convertMessagesToCacheControlledMessages,
  trackCachePerformance,
} from "../../../../utils/caching.js";
import { createMarkTaskCompletedToolFields } from "@open-swe/shared/open-swe/tools";
import { HumanMessage } from "@langchain/core/messages";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

const formatDynamicContextPrompt = (state: GraphState) => {
  const planString = getActivePlanItems(state.taskPlan)
    .map((i) => `<plan-item index="${i.index}">\n${i.plan}\n</plan-item>`)
    .join("\n");
  return DYNAMIC_SYSTEM_PROMPT.replaceAll("{PLAN_PROMPT}", planString)
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

const planSpecificPrompt = `<detailed_plan_information>
Here is the task execution plan for the request you're working on.
Ensure you carefully read through all of the instructions, messages, and context provided above.
Once you have a clear understanding of the current state of the task, analyze the plan provided below, and take an action based on it.
You're provided with the full list of tasks, including the completed, current and remaining tasks.

You are in the process of executing the current task:

{PLAN_PROMPT}
</detailed_plan_information>`;

const formatSpecificPlanPrompt = (state: GraphState): HumanMessage => {
  return new HumanMessage({
    id: uuidv4(),
    content: planSpecificPrompt.replace(
      "{PLAN_PROMPT}",
      formatPlanPrompt(getActivePlanItems(state.taskPlan)),
    ),
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
  const markTaskCompletedTool = createMarkTaskCompletedToolFields();

  const tools = [
    createSearchTool(state),
    {
      type: "text_editor_20250429",
      name: "str_replace_based_edit_tool"
    },
    createShellTool(state),
    createApplyPatchTool(state),
    createRequestHumanHelpToolFields(),
    createUpdatePlanToolFields(),
    createGetURLContentTool(state),
    createInstallDependenciesTool(state),
    markTaskCompletedTool,
    createSearchDocumentForTool(state, config),
    ...mcpTools,
  ];
  logger.info(
    `MCP tools added to Programmer: ${mcpTools.map((t) => t.name).join(", ")}`,
  );
  // Cache Breakpoint 1: Add cache_control marker to the last tool for tools definition caching
  tools[tools.length - 1] = {
    ...tools[tools.length - 1],
    cache_control: { type: "ephemeral" },
  } as any;

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

  const inputMessagesWithCache =
    convertMessagesToCacheControlledMessages(inputMessages);
  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatCacheablePrompt({
        ...state,
        taskPlan: latestTaskPlan ?? state.taskPlan,
      }),
    },
    ...inputMessagesWithCache,
    formatSpecificPlanPrompt(state),
  ]);

  const hasToolCalls = !!response.tool_calls?.length;
  // No tool calls means the graph is going to end. Stop the sandbox.
  let newSandboxSessionId: string | undefined;
  if (!hasToolCalls && state.sandboxSessionId) {
    logger.info("No tool calls found. Stopping sandbox...");
    newSandboxSessionId = await stopSandbox(state.sandboxSessionId);
  }

  if (
    response.tool_calls?.length &&
    response.tool_calls?.length > 1 &&
    response.tool_calls.some((t) => t.name === markTaskCompletedTool.name)
  ) {
    logger.error(
      "Multiple tool calls found, including mark_task_completed. Removing the mark_task_completed call.",
      {
        toolCalls: JSON.stringify(response.tool_calls, null, 2),
      },
    );
    response.tool_calls = response.tool_calls.filter(
      (t) => t.name !== markTaskCompletedTool.name,
    );
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
