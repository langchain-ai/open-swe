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
  INSTALL_DEPENDENCIES_TOOL_PROMPT,
  SYSTEM_PROMPT,
} from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createSearchTool } from "../../../../tools/search.js";
import { createInstallDependenciesTool } from "../../../../tools/install-dependencies.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { getMcpTools } from "../../../../utils/mcp-client.js";
import { BaseMessage, isAIMessage } from "@langchain/core/messages";
import { createCodeReviewMarkTaskNotCompleteFields } from "@open-swe/shared/open-swe/tools";
import { z } from "zod";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

const formatCodeReviewPrompt = (
  review: string,
  newActions: string[],
): string => {
  return CODE_REVIEW_PROMPT.replaceAll("{CODE_REVIEW}", review).replaceAll(
    "{CODE_REVIEW_ACTIONS}",
    newActions.map((a) => `* ${a}`).join("\n"),
  );
};

const getCodeReviewFields = (
  messages: BaseMessage[],
): { review: string; newActions: string[] } | null => {
  const codeReviewToolFields = createCodeReviewMarkTaskNotCompleteFields();
  const codeReviewMessage = messages
    .filter(isAIMessage)
    .findLast(
      (m) =>
        m.tool_calls?.length &&
        m.tool_calls.some((tc) => tc.name === codeReviewToolFields.name),
    );
  const codeReviewToolCall = codeReviewMessage?.tool_calls?.find(
    (tc) => tc.name === codeReviewToolFields.name,
  );
  if (!codeReviewMessage || !codeReviewToolCall) return null;
  const codeReviewArgs = codeReviewToolCall.args as z.infer<
    typeof codeReviewToolFields.schema
  >;
  if (!codeReviewArgs.review || !codeReviewArgs.additional_actions?.length)
    return null;

  return {
    review: codeReviewArgs.review,
    newActions: codeReviewArgs.additional_actions,
  };
};

const formatPrompt = (state: GraphState): string => {
  const repoDirectory = getRepoAbsolutePath(state.targetRepository);
  const activePlanItems = getActivePlanItems(state.taskPlan);
  const currentPlanItem = activePlanItems
    .filter((p) => !p.completed)
    .sort((a, b) => a.index - b.index)[0];
  const codeReview = getCodeReviewFields(state.internalMessages);
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
    .replaceAll("{CUSTOM_RULES}", formatCustomRulesPrompt(state.customRules))
    .replaceAll(
      "{CODE_REVIEW_PROMPT}",
      codeReview
        ? formatCodeReviewPrompt(codeReview.review, codeReview.newActions)
        : "",
    );
};

export async function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.ACTION_GENERATOR);
  const mcpTools = await getMcpTools(config);

  const tools = [
    createSearchTool(state),
    createShellTool(state),
    createApplyPatchTool(state),
    createRequestHumanHelpToolFields(),
    createUpdatePlanToolFields(),
    createGetURLContentTool(),
    ...mcpTools,
    // Only provide the dependencies installed tool if they're not already installed.
    ...(state.dependenciesInstalled
      ? []
      : [createInstallDependenciesTool(state)]),
  ];
  logger.info(
    `MCP tools added to Programmer: ${mcpTools.map((t) => t.name).join(", ")}`,
  );

  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    parallel_tool_calls: true,
  });

  const [missingMessages, { taskPlan: latestTaskPlan }] = await Promise.all([
    getMissingMessages(state, config),
    getPlansFromIssue(state, config),
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
