import { loadModel, Task } from "../../../../utils/load-model.js";
import { createShellTool } from "../../../../tools/index.js";
import {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@open-swe/shared/open-swe/reviewer/types";
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
import { getUserRequest } from "../../../../utils/user-request.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { formatPlanPromptWithSummaries } from "../../../../utils/plan-prompt.js";

const logger = createLogger(LogLevel.INFO, "GeneratePlanningMessageNode");

function formatSystemPrompt(state: ReviewerGraphState): string {
  const userRequest = getUserRequest(state.messages);
  const activePlan = getActivePlanItems(state.taskPlan);
  const tasksString = formatPlanPromptWithSummaries(activePlan);

  return SYSTEM_PROMPT.replaceAll(
    "{CODEBASE_TREE}",
    state.codebaseTree || "No codebase tree generated yet.",
  )
    .replaceAll(
      "{CURRENT_WORKING_DIRECTORY}",
      getRepoAbsolutePath(state.targetRepository),
    )
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replaceAll("{CHANGED_FILES}", state.changedFiles)
    .replaceAll("{HEAD_BRANCH_NAME}", state.headBranchName)
    .replaceAll("{COMPLETED_TASKS_AND_SUMMARIES}", tasksString)
    .replaceAll("{USER_REQUEST}", userRequest);
}

export async function generateReviewActions(
  state: ReviewerGraphState,
  config: GraphConfig,
): Promise<ReviewerGraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [createRgTool(state), createShellTool(state)];
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
