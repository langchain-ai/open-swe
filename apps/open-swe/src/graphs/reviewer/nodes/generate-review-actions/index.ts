import { loadModel, Task } from "../../../../utils/load-model.js";
import { createShellTool } from "../../../../tools/index.js";
import {
  PlannerGraphState,
  PlannerGraphUpdate,
} from "@open-swe/shared/open-swe/planner/types";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { SYSTEM_PROMPT } from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { filterHiddenMessages } from "../../../../utils/message/filter-hidden.js";
import { getTaskPlanFromIssue } from "../../../../utils/github/issue-task.js";
import { createRgTool } from "../../../../tools/rg.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

function formatSystemPrompt(state: PlannerGraphState): string {
  return SYSTEM_PROMPT
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll(
      "{CURRENT_WORKING_DIRECTORY}",
      getRepoAbsolutePath(state.targetRepository),
    )
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replaceAll("{CHANGED_FILES}", "TODO: ADD")
    .replaceAll("{HEAD_BRANCH_NAME}", "TODO: ADD")
    .replaceAll("{COMPLETED_TASKS_AND_SUMMARIES}", "TODO: ADD")
    .replaceAll("{USER_REQUEST}", "TODO: ADD");
}

export async function generateReviewActions(
  state: PlannerGraphState,
  config: GraphConfig,
): Promise<PlannerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [
    createRgTool(state),
    createShellTool(state),
  ];
  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    parallel_tool_calls: true,
  });

  const [missingMessages, latestTaskPlan] = await Promise.all([
    getMissingMessages(state, config),
    getTaskPlanFromIssue(state, config),
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
