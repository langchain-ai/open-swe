import { loadModel, Task } from "../../../../utils/load-model.js";
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
import { filterHiddenMessages } from "../../../../utils/message/filter-hidden.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createRgTool } from "../../../../tools/search.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { createPlannerNotesTool } from "../../../../tools/planner-notes.js";
import { createFindInstancesOfTool } from "../../../../tools/find-instances-of.js";
import { getMcpTools } from "../../../../utils/mcp-client.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

function formatSystemPrompt(state: PlannerGraphState): string {
  // It's a followup if there's more than one human message.
  const isFollowup = isFollowupRequest(state.taskPlan, state.proposedPlan);
  return SYSTEM_PROMPT.replace(
    "{FOLLOWUP_MESSAGE_PROMPT}",
    isFollowup
      ? formatFollowupMessagePrompt(state.taskPlan, state.proposedPlan)
      : "",
  )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll(
      "{CURRENT_WORKING_DIRECTORY}",
      getRepoAbsolutePath(state.targetRepository),
    )
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules));
}

export async function generateAction(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const mcpTools = await getMcpTools(config);

  const tools = [
    createRgTool(state),
    createShellTool(state),
    createFindInstancesOfTool(state),
    createPlannerNotesTool(),
    createGetURLContentTool(),
    ...mcpTools,
  ];
  logger.info(
    `MCP tools added to Planner: ${mcpTools.map((t) => t.name).join(", ")}`,
  );

  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    parallel_tool_calls: true,
  });

  const [missingMessages, { taskPlan: latestTaskPlan }] = await Promise.all([
    getMissingMessages(state, config),
    getPlansFromIssue(state, config),
  ]);
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
      ...filterHiddenMessages(state.messages),
      ...missingMessages,
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
  };
}
