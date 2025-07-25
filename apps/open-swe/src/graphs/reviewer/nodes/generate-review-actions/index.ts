import {
  loadModel,
  supportsParallelToolCallsParam,
  Task,
} from "../../../../utils/llms/index.js";
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
import { formatUserRequestPrompt } from "../../../../utils/user-request.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { formatPlanPromptWithSummaries } from "../../../../utils/plan-prompt.js";
import {
  formatCodeReviewPrompt,
  getCodeReviewFields,
} from "../../../../utils/review.js";
import { BaseMessage } from "@langchain/core/messages";
import { getMessageString } from "../../../../utils/message/content.js";
import {
  CacheablePromptSegment,
  convertMessagesToCacheControlledMessages,
  trackCachePerformance,
} from "../../../../utils/caching.js";
import { createScratchpadTool } from "../../../../tools/scratchpad.js";

const logger = createLogger(LogLevel.INFO, "GenerateReviewActionsNode");

function formatSystemPrompt(state: ReviewerGraphState): string {
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
    .replaceAll("{BASE_BRANCH_NAME}", state.baseBranchName)
    .replaceAll("{COMPLETED_TASKS_AND_SUMMARIES}", tasksString)
    .replaceAll(
      "{DEPENDENCIES_INSTALLED}",
      state.dependenciesInstalled ? "Yes" : "No",
    )
    .replaceAll(
      "{USER_REQUEST_PROMPT}",
      formatUserRequestPrompt(state.messages),
    );
}

const formatCacheablePrompt = (
  state: ReviewerGraphState,
): CacheablePromptSegment[] => {
  const codeReview = getCodeReviewFields(state.internalMessages);

  const segments: CacheablePromptSegment[] = [
    {
      type: "text",
      text: formatSystemPrompt(state),
      cache_control: { type: "ephemeral" },
    },
  ];

  // Cache Breakpoint 4: Code Review Context (only add if present)
  if (codeReview) {
    segments.push({
      type: "text",
      text: formatCodeReviewPrompt(PREVIOUS_REVIEW_PROMPT, {
        review: codeReview.review,
        newActions: codeReview.newActions,
      }),
      cache_control: { type: "ephemeral" },
    });
  }

  return segments.filter((segment) => segment.text.trim() !== "");
};

function formatUserConversationHistoryMessage(
  messages: BaseMessage[],
): CacheablePromptSegment[] {
  return [
    {
      type: "text",
      text: `Here is the full conversation history of the programmer. This includes all of the actions taken by the programmer, as well as any user input.
If the history has been truncated, it is because the conversation was too long. In this case, you should only consider the most recent messages.

<conversation_history>
${messages.map(getMessageString).join("\n")}
</conversation_history>`,
      cache_control: { type: "ephemeral" },
    },
  ];
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
    createScratchpadTool(
      "when generating a final review, after all context gathering and reviewing is complete",
    ),
  ];
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

  const reviewerMessagesWithCache = convertMessagesToCacheControlledMessages(
    state.reviewerMessages,
  );
  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatCacheablePrompt(state),
    },
    {
      role: "user",
      content: formatUserConversationHistoryMessage(state.internalMessages),
    },
    ...reviewerMessagesWithCache,
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
    tokenData: trackCachePerformance(response),
  };
}
