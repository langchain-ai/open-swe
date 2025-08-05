import { v4 as uuidv4 } from "uuid";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
  TaskPlan,
} from "@open-swe/shared/open-swe/types";
import {
  getModelManager,
  loadModel,
  Provider,
  supportsParallelToolCallsParam,
} from "../../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import {
  createShellTool,
  createApplyPatchTool,
  createRequestHumanHelpToolFields,
  createUpdatePlanToolFields,
  createGetURLContentTool,
  createSearchDocumentForTool,
  createWriteDefaultTsConfigTool,
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
  STATIC_ANTHROPIC_SYSTEM_INSTRUCTIONS,
  STATIC_SYSTEM_INSTRUCTIONS,
} from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createGrepTool } from "../../../../tools/grep.js";
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
import {
  BaseMessage,
  BaseMessageLike,
  HumanMessage,
} from "@langchain/core/messages";
import { BindToolsInput } from "@langchain/core/language_models/chat_models";

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

const formatStaticInstructionsPrompt = (
  state: GraphState,
  isAnthropicModel: boolean,
) => {
  return (
    isAnthropicModel
      ? STATIC_ANTHROPIC_SYSTEM_INSTRUCTIONS
      : STATIC_SYSTEM_INSTRUCTIONS
  )
    .replaceAll("{REPO_DIRECTORY}", getRepoAbsolutePath(state.targetRepository))
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules));
};

const formatCacheablePrompt = (
  state: GraphState,
  args?: {
    isAnthropicModel?: boolean;
    excludeCacheControl?: boolean;
  },
): CacheablePromptSegment[] => {
  const codeReview = getCodeReviewFields(state.internalMessages);

  const segments: CacheablePromptSegment[] = [
    // Cache Breakpoint 2: Static Instructions
    {
      type: "text",
      text: formatStaticInstructionsPrompt(state, !!args?.isAnthropicModel),
      ...(!args?.excludeCacheControl
        ? { cache_control: { type: "ephemeral" } }
        : {}),
    },

    // Cache Breakpoint 3: Dynamic Context
    {
      type: "text",
      text: formatDynamicContextPrompt(state),
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
      ...(!args?.excludeCacheControl
        ? { cache_control: { type: "ephemeral" } }
        : {}),
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

async function createToolsAndPrompt(
  state: GraphState,
  config: GraphConfig,
  options: {
    latestTaskPlan: TaskPlan | null;
    missingMessages: BaseMessage[];
  },
): Promise<{
  providerTools: Record<Provider, BindToolsInput[]>;
  providerMessages: Record<Provider, BaseMessageLike[]>;
}> {
  const mcpTools = await getMcpTools(config);
  const sharedTools = [
    createGrepTool(state, config),
    createShellTool(state, config),
    createRequestHumanHelpToolFields(),
    createUpdatePlanToolFields(),
    createGetURLContentTool(state),
    createInstallDependenciesTool(state, config),
    createMarkTaskCompletedToolFields(),
    createSearchDocumentForTool(state, config),
    createWriteDefaultTsConfigTool(state, config),
    ...mcpTools,
  ];

  logger.info(
    `MCP tools added to Programmer: ${mcpTools.map((t) => t.name).join(", ")}`,
  );

  const anthropicModelTools = [
    ...sharedTools,
    {
      type: "text_editor_20250429",
      name: "str_replace_based_edit_tool",
      cache_control: { type: "ephemeral" },
    },
  ];
  const nonAnthropicModelTools = [
    ...sharedTools,
    {
      ...createApplyPatchTool(state, config),
      cache_control: { type: "ephemeral" },
    },
  ];

  const inputMessages = filterMessagesWithoutContent([
    ...state.internalMessages,
    ...options.missingMessages,
  ]);
  if (!inputMessages.length) {
    throw new Error("No messages to process.");
  }

  const anthropicMessages = [
    {
      role: "system",
      content: formatCacheablePrompt(
        {
          ...state,
          taskPlan: options.latestTaskPlan ?? state.taskPlan,
        },
        {
          isAnthropicModel: true,
          excludeCacheControl: false,
        },
      ),
    },
    ...convertMessagesToCacheControlledMessages(inputMessages),
    formatSpecificPlanPrompt(state),
  ];

  const nonAnthropicMessages = [
    {
      role: "system",
      content: formatCacheablePrompt(
        {
          ...state,
          taskPlan: options.latestTaskPlan ?? state.taskPlan,
        },
        {
          isAnthropicModel: false,
          excludeCacheControl: true,
        },
      ),
    },
    ...inputMessages,
    formatSpecificPlanPrompt(state),
  ];

  return {
    providerTools: {
      anthropic: anthropicModelTools,
      openai: nonAnthropicModelTools,
      "google-genai": nonAnthropicModelTools,
    },
    providerMessages: {
      anthropic: anthropicMessages,
      openai: nonAnthropicMessages,
      "google-genai": nonAnthropicMessages,
    },
  };
}

export async function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(
    config,
    LLMTask.PROGRAMMER,
  );
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.PROGRAMMER,
  );
  const markTaskCompletedTool = createMarkTaskCompletedToolFields();
  const isAnthropicModel = modelName.includes("claude-");

  const [missingMessages, { taskPlan: latestTaskPlan }] = await Promise.all([
    getMissingMessages(state, config),
    getPlansFromIssue(state, config),
  ]);

  const { providerTools, providerMessages } = await createToolsAndPrompt(
    state,
    config,
    {
      latestTaskPlan,
      missingMessages,
    },
  );

  const model = await loadModel(config, LLMTask.PROGRAMMER, {
    providerTools: providerTools,
    providerMessages: providerMessages,
  });

  const modelWithTools = model.bindTools(
    isAnthropicModel ? providerTools.anthropic : providerTools.openai,
    {
      tool_choice: "auto",
      ...(modelSupportsParallelToolCallsParam
        ? {
            parallel_tool_calls: true,
          }
        : {}),
    },
  );
  const response = await modelWithTools.invoke(
    isAnthropicModel ? providerMessages.anthropic : providerMessages.openai,
  );

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
      `Multiple tool calls found, including ${markTaskCompletedTool.name}. Removing the ${markTaskCompletedTool.name} call.`,
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
    tokenData: trackCachePerformance(response, modelName),
  };
}
