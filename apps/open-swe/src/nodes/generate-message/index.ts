import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import { loadModel, Task } from "../../utils/load-model.js";
import {
  createShellTool,
  createApplyPatchTool,
  requestHumanHelpTool,
  updatePlanTool,
} from "../../tools/index.js";
import { getRepoAbsolutePath } from "../../utils/git.js";
import { formatPlanPrompt } from "../../utils/plan-prompt.js";
import { stopSandbox } from "../../utils/sandbox.js";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getCurrentPlanItem } from "../../utils/current-task.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { SYSTEM_PROMPT } from "./prompt.js";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

const formatPrompt = (state: GraphState): string => {
  const repoDirectory = getRepoAbsolutePath(state.targetRepository);
  const activePlanItems = getActivePlanItems(state.plan);
  const currentPlanItem = activePlanItems
    .filter((p) => !p.completed)
    .sort((a, b) => a.index - b.index)[0];
  return SYSTEM_PROMPT.replaceAll(
    "{PLAN_PROMPT_WITH_SUMMARIES}",
    formatPlanPrompt(getActivePlanItems(state.plan), {
      includeSummaries: true,
    }),
  )
    .replaceAll(
      "{PLAN_PROMPT}",
      formatPlanPrompt(getActivePlanItems(state.plan)),
    )
    .replaceAll("{REPO_DIRECTORY}", repoDirectory)
    .replaceAll(
      "{PLAN_GENERATION_SUMMARY}",
      `<plan-generation-summary>\n${state.planContextSummary}\n</plan-generation-summary>`,
    )
    .replaceAll(
      "{CODEBASE_TREE}",
      state.codebaseTree || "No codebase tree generated yet.",
    )
    .replaceAll("{CURRENT_WORKING_DIRECTORY}", repoDirectory)
    .replaceAll("{CURRENT_TASK_NUMBER}", currentPlanItem.index.toString());
};

export async function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const tools = [
    createShellTool(state),
    createApplyPatchTool(state),
    requestHumanHelpTool,
    updatePlanTool,
  ];
  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    parallel_tool_calls: false,
  });

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt(state),
    },
    ...state.internalMessages,
  ]);

  const hasToolCalls = !!response.tool_calls?.length;
  // No tool calls means the graph is going to end. Stop the sandbox.
  let newSandboxSessionId: string | undefined;
  if (!hasToolCalls && state.sandboxSessionId) {
    logger.info("No tool calls found. Stopping sandbox...");
    newSandboxSessionId = await stopSandbox(state.sandboxSessionId);
  }

  logger.info("Generated action", {
    currentTask: getCurrentPlanItem(getActivePlanItems(state.plan)).plan,
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
    ...(response.tool_calls?.[0] && {
      name: response.tool_calls?.[0].name,
      args: response.tool_calls?.[0].args,
    }),
  });

  return {
    messages: [response],
    internalMessages: [response],
    ...(newSandboxSessionId && { sandboxSessionId: newSandboxSessionId }),
  };
}
