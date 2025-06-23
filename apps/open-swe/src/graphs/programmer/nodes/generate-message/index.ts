import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import { loadModel, Task } from "../../../../utils/load-model.js";
import {
  createShellTool,
  createApplyPatchTool,
  createRequestHumanHelpToolFields,
  createUpdatePlanToolFields,
} from "../../../../tools/index.js";
import { formatPlanPrompt } from "../../../../utils/plan-prompt.js";
import { stopSandbox } from "../../../../utils/sandbox.js";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getCurrentPlanItem } from "../../../../utils/current-task.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import {
  DEPENDENCIES_INSTALLED_PROMPT,
  INSTALL_DEPENDENCIES_TOOL_PROMPT,
  SYSTEM_PROMPT,
} from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { getTaskPlanFromIssue } from "../../../../utils/github/issue-task.js";
import { createRgTool } from "../../../../tools/rg.js";
import { createInstallDependenciesTool } from "../../../../tools/install-dependencies.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

const formatPrompt = (state: GraphState): string => {
  const repoDirectory = getRepoAbsolutePath(state.targetRepository);
  const activePlanItems = getActivePlanItems(state.taskPlan);
  const currentPlanItem = activePlanItems
    .filter((p) => !p.completed)
    .sort((a, b) => a.index - b.index)[0];
  return SYSTEM_PROMPT.replaceAll(
    "{PLAN_PROMPT_WITH_SUMMARIES}",
    formatPlanPrompt(getActivePlanItems(state.taskPlan), {
      includeSummaries: true,
    }),
  )
    .replaceAll(
      "{PLAN_PROMPT}",
      formatPlanPrompt(getActivePlanItems(state.taskPlan)),
    )
    .replaceAll("{REPO_DIRECTORY}", repoDirectory)
    .replaceAll(
      "{PLAN_GENERATION_NOTES}",
      `<plan-generation-notes>\n${state.contextGatheringNotes}\n</plan-generation-notes>`,
    )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll("{CURRENT_WORKING_DIRECTORY}", repoDirectory)
    .replaceAll("{CURRENT_TASK_NUMBER}", currentPlanItem.index.toString())
    .replaceAll(
      "{INSTALL_DEPENDENCIES_TOOL_PROMPT}",
      !state.dependenciesInstalled
        ? INSTALL_DEPENDENCIES_TOOL_PROMPT
        : DEPENDENCIES_INSTALLED_PROMPT,
    )
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules));
};

export async function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [
    createRgTool(state),
    createShellTool(state),
    createApplyPatchTool(state),
    createRequestHumanHelpToolFields(),
    createUpdatePlanToolFields(),
    // Only provide the dependencies installed tool if they're not already installed.
    ...(state.dependenciesInstalled
      ? []
      : [createInstallDependenciesTool(state)]),
  ];
  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    parallel_tool_calls: true,
  });

  const [missingMessages, latestTaskPlan] = await Promise.all([
    getMissingMessages(state, config),
    getTaskPlanFromIssue(state, config),
  ]);

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt({
        ...state,
        taskPlan: latestTaskPlan ?? state.taskPlan,
      }),
    },
    ...state.internalMessages,
    ...missingMessages,
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
  };
}
