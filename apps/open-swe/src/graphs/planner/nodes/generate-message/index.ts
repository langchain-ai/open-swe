import {
  loadModel,
  supportsParallelToolCallsParam,
  Task,
} from "../../../../utils/load-model.js";
import {
  createGetURLContentTool,
  createShellTool,
} from "../../../../tools/index.js";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import {
  formatFollowupMessagePrompt,
  isFollowupRequest,
} from "../../utils/followup.js";
import { SYSTEM_PROMPT } from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createSearchTool } from "../../../../tools/search.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { createPlannerNotesTool } from "../../../../tools/planner-notes.js";
import { getMcpTools } from "../../../../utils/mcp-client.js";
import { filterMessagesWithoutContent } from "../../../../utils/message/content.js";
import { getPlannerNotes } from "../../utils/get-notes.js";
import { formatUserRequestPrompt } from "../../../../utils/user-request.js";
import {
  convertMessagesToCacheControlledMessages,
  trackCachePerformance,
} from "../../../../utils/caching.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  const plannerNotes = getPlannerNotes(state.messages)
    .map((n) => `- ${n}`)
    .join("\n");
  return SYSTEM_PROMPT.replace(
    "{FOLLOWUP_MESSAGE_PROMPT}",
    isFollowup
      ? formatFollowupMessagePrompt(
          state.taskPlan,
          state.proposedPlan,
          plannerNotes,
        )
      : "",
  )
    .replaceAll(
      "{CURRENT_WORKING_DIRECTORY}",
      getRepoAbsolutePath(state.targetRepository),
    )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replace("{USER_REQUEST_PROMPT}", formatUserRequestPrompt(state.messages));
}

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.PROGRAMMER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    Task.PROGRAMMER,
  );
  const mcpTools = await getMcpTools(config);

  const tools = [
    createSearchTool(state),
    createShellTool(state),
    createPlannerNotesTool(),
    createGetURLContentTool(),
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

  const [missingMessages, { taskPlan: latestTaskPlan }] = await Promise.all([
    getMissingMessages(state, config),
    getPlansFromIssue(state, config),
  ]);

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
        content: formatSystemPrompt({
          ...state,
          taskPlan: latestTaskPlan ?? state.taskPlan,
        }),
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
    tokenData: trackCachePerformance(response),
  };
}
