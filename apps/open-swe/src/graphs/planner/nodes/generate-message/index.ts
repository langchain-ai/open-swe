import {
  getModelManager,
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../../utils/llms/index.js";
import { LLMTask } from "@openswe/shared/open-swe/llm-task";
import {
  createGetURLContentTool,
  createShellTool,
  createSearchDocumentForTool,
} from "../../../../tools/index.js";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@openswe/shared/open-swe/planner/types";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getMessageContentString } from "@openswe/shared/messages";
import {
  formatFollowupMessagePrompt,
  isFollowupRequest,
} from "../../utils/followup.js";
import {
  SYSTEM_PROMPT,
  EXTERNAL_FRAMEWORK_DOCUMENTATION_PROMPT,
  EXTERNAL_FRAMEWORK_PLAN_PROMPT,
} from "./prompt.js";
import { getRepoAbsolutePath } from "@openswe/shared/git";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@openswe/shared/open-swe/local-mode";
import {
  getIssueService,
  getMissingMessages,
} from "../../../../services/issue-service.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createGrepTool } from "../../../../tools/grep.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { createScratchpadTool } from "../../../../tools/scratchpad.js";
import { getMcpTools } from "../../../../utils/mcp-client.js";
import { filterMessagesWithoutContent } from "../../../../utils/message/content.js";
import { getScratchpad } from "../../utils/scratchpad-notes.js";
import { formatUserRequestPrompt } from "../../../../utils/user-request.js";
import {
  convertMessagesToCacheControlledMessages,
  trackCachePerformance,
} from "../../../../utils/caching.js";
import { createViewTool } from "../../../../tools/builtin-tools/view.js";
import { shouldCreateIssue } from "../../../../utils/should-create-issue.js";
import { shouldUseCustomFramework } from "../../../../utils/should-use-custom-framework.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

function formatSystemPrompt(
  state: PlannerGraphState,
  config: GraphConfig,
): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  const scratchpad = getScratchpad(state.messages)
    .map((n) => `- ${n}`)
    .join("\n");
  return SYSTEM_PROMPT.replace(
    "{FOLLOWUP_MESSAGE_PROMPT}",
    isFollowup
      ? formatFollowupMessagePrompt(
          state.taskPlan,
          state.proposedPlan,
          scratchpad,
        )
      : "",
  )
    .replaceAll(
      "{CURRENT_WORKING_DIRECTORY}",
      isLocalMode(config)
        ? getLocalWorkingDirectory()
        : getRepoAbsolutePath(state.targetRepository),
    )
    .replaceAll(
      "{LOCAL_MODE_NOTE}",
      isLocalMode(config)
        ? "<local_mode_note>IMPORTANT: You are running in local mode. When specifying file paths, use relative paths from the current working directory or absolute paths that start with the current working directory. Do NOT use sandbox paths like '/home/daytona/project/'.</local_mode_note>"
        : "",
    )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replace("{USER_REQUEST_PROMPT}", formatUserRequestPrompt(state.messages))
    .replace(
      "{EXTERNAL_FRAMEWORK_DOCUMENTATION_PROMPT}",
      shouldUseCustomFramework(config)
        ? EXTERNAL_FRAMEWORK_DOCUMENTATION_PROMPT
        : "",
    )
    .replace(
      "{EXTERNAL_FRAMEWORK_PLAN_PROMPT}",
      shouldUseCustomFramework(config) ? EXTERNAL_FRAMEWORK_PLAN_PROMPT : "",
    )
    .replace("{DEV_SERVER_PROMPT}", ""); // Always empty until we add dev server tool
}

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, LLMTask.PLANNER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(config, LLMTask.PLANNER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.PLANNER,
  );
  const mcpTools = await getMcpTools(config);

  const tools = [
    createGrepTool(state, config),
    createShellTool(state, config),
    createViewTool(state, config),
    createScratchpadTool(
      "when generating a final plan, after all context gathering is complete",
    ),
    createGetURLContentTool(state),
    createSearchDocumentForTool(state, config),
    ...mcpTools,
  ];
  logger.info(
    `MCP tools added to Planner: ${mcpTools.map((t) => t.name).join(", ")}`,
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

  const [missingMessages, { taskPlan: latestTaskPlan }] =
    shouldCreateIssue(config) && state.githubIssueId
      ? await Promise.all([
          getMissingMessages(getIssueService(config), {
            messages: state.messages,
            issueId: state.githubIssueId,
            repo: state.targetRepository,
          }),
          getPlansFromIssue(state, config),
        ])
      : [[], { taskPlan: null }];

  const inputMessages = filterMessagesWithoutContent([
    ...state.messages,
    ...missingMessages,
  ]);
  if (!inputMessages.length) {
    throw new Error("No messages to process.");
  }

  const inputMessagesWithCache =
    convertMessagesToCacheControlledMessages(inputMessages);
  const response = await modelWithTools
    .withConfig({ tags: ["nostream"] })
    .invoke([
      {
        role: "system",
        content: formatSystemPrompt(
          {
            ...state,
            taskPlan: latestTaskPlan ?? state.taskPlan,
          },
          config,
        ),
      },
      ...inputMessagesWithCache,
    ]);

  logger.info("Generated planning message", {
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
    ...response.tool_calls?.map((tc) => ({
      name: tc.name,
      args: tc.args,
    })),
  });

  return {
    messages: [...missingMessages, response],
    ...(latestTaskPlan && { taskPlan: latestTaskPlan }),
    tokenData: trackCachePerformance(response, modelName),
  };
}
