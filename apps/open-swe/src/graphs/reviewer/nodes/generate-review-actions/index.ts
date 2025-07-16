import {
  loadModel,
  supportsParallelToolCallsParam,
  Task,
} from "../../../../utils/load-model.js";
import {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@open-swe/shared/open-swe/reviewer/types";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../../../utils/logger.js";
import { getMessageContentString } from "@open-swe/shared/messages";
import { PREVIOUS_REVIEW_PROMPT, SYSTEM_PROMPT } from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import {
  createSearchTool,
  createShellTool,
  createInstallDependenciesTool,
} from "../../../../tools/index.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { getUserRequest } from "../../../../utils/user-request.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { formatPlanPromptWithSummaries } from "../../../../utils/plan-prompt.js";
import {
  formatCodeReviewPrompt,
  getCodeReviewFields,
} from "../../../../utils/review.js";
import { BaseMessage } from "@langchain/core/messages";
import { getMessageString } from "../../../../utils/message/content.js";

const logger = createLogger(LogLevel.INFO, "GenerateReviewActionsNode");

function formatSystemPrompt(state: ReviewerGraphState): string {
  const userRequest = getUserRequest(state.messages);
  const activePlan = getActivePlanItems(state.taskPlan);
  const tasksString = formatPlanPromptWithSummaries(activePlan);
  const codeReview = getCodeReviewFields(state.internalMessages);

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
    .replaceAll("{BASE_BRANCH_NAME}", state.baseBranchName)
    .replaceAll("{COMPLETED_TASKS_AND_SUMMARIES}", tasksString)
    .replaceAll(
      "{DEPENDENCIES_INSTALLED}",
      state.dependenciesInstalled ? "Yes" : "No",
    )
    .replaceAll("{USER_REQUEST}", userRequest)
    .replaceAll(
      "{PREVIOUS_REVIEW_PROMPT}",
      codeReview
        ? formatCodeReviewPrompt(PREVIOUS_REVIEW_PROMPT, {
            review: codeReview.review,
            newActions: codeReview.newActions,
          })
        : "",
    );
}

function formatUserConversationHistoryMessage(messages: BaseMessage[]): string {
  return `Here is the full conversation history of the programmer. This includes all of the actions taken by the programmer, as well as any user input.
If the history has been truncated, it is because the conversation was too long. In this case, you should only consider the most recent messages.

<conversation_history>
${messages.map(getMessageString).join("\n")}
</conversation_history>`;
}

export async function generateReviewActions(
  state: ReviewerGraphState,
  config: GraphConfig,
): Promise<ReviewerGraphUpdate> {
  const model = await loadModel(config, Task.PROGRAMMER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    Task.PROGRAMMER,
  );
  const tools = [
    createSearchTool(state),
    createShellTool(state),
    createInstallDependenciesTool(state),
  ];
  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: true,
        }
      : {}),
  });

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatSystemPrompt(state),
    },
    {
      role: "user",
      content: formatUserConversationHistoryMessage(state.internalMessages),
    },
    ...state.reviewerMessages,
  ]);

  logger.info("Generated review actions", {
    ...(getMessageContentString(response.content) && {
      content: getMessageContentString(response.content),
    }),
    ...response.tool_calls?.map((tc) => ({
      name: tc.name,
      args: tc.args,
    })),
  });

  return {
    messages: [response],
    reviewerMessages: [response],
  };
}
