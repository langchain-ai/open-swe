import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import {
  loadModel,
  supportsParallelToolCallsParam,
  Task,
} from "../../../../utils/load-model.js";
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
  STATIC_SYSTEM_INSTRUCTIONS,
} from "./prompt.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getMissingMessages } from "../../../../utils/github/issue-messages.js";
import { getPlansFromIssue } from "../../../../utils/github/issue-task.js";
import { createSearchTool } from "../../../../tools/search.js";
import { createInstallDependenciesTool } from "../../../../tools/install-dependencies.js";
import { formatCustomRulesPrompt } from "../../../../utils/custom-rules.js";
import { getMcpTools } from "../../../../utils/mcp-client.js";
import {
  formatCodeReviewPrompt,
  getCodeReviewFields,
} from "../../../../utils/review.js";
import { filterMessagesWithoutContent } from "../../../../utils/message/content.js";

const logger = createLogger(LogLevel.INFO, "GenerateMessageNode");

interface CacheablePromptSegment {
  type: "text";
  text: string;
  cache_control?: { type: "ephemeral" };
}

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
        ? formatCodeReviewPrompt(CODE_REVIEW_PROMPT, {
            review: codeReview.review,
            newActions: codeReview.newActions,
          })
        : "",
    );
};

const formatCacheablePrompt = (state: GraphState): CacheablePromptSegment[] => {
  const repoDirectory = getRepoAbsolutePath(state.targetRepository);
  const activePlanItems = getActivePlanItems(state.taskPlan);
  const currentPlanItem = activePlanItems
    .filter((p) => !p.completed)
    .sort((a, b) => a.index - b.index)[0];
  const codeReview = getCodeReviewFields(state.internalMessages);

  const segments: CacheablePromptSegment[] = [
    // Cache Breakpoint 2: Static Instructions
    {
      type: "text",
      text: STATIC_SYSTEM_INSTRUCTIONS,
      cache_control: { type: "ephemeral" }
    },
    
    // Cache Breakpoint 3: Dynamic Context
    {
      type: "text",
      text: `# Context

<plan_information>
## Generated Plan with Summaries
${formatPlanPrompt(activePlanItems, { includeSummaries: true })}

## Plan Generation Notes
These are notes you took while gathering context for the plan:
<plan-generation-notes>
${state.contextGatheringNotes || "No context gathering notes available."}
</plan-generation-notes>

## Current Task Statuses
${formatPlanPrompt(activePlanItems)}
</plan_information>

<codebase_structure>
## Codebase Tree (3 levels deep, respecting .gitignore)
Generated via: \`git ls-files | tree --fromfile -L 3\`
Location: ${repoDirectory}

${state.codebaseTree || "No codebase tree generated yet."}
</codebase_structure>

${formatCustomRulesPrompt(state.customRules)}`,
      cache_control: { type: "ephemeral" }
    }
  ];

  // Cache Breakpoint 4: Code Review Context (only add if present)
  if (codeReview) {
    segments.push({
      type: "text",
      text: formatCodeReviewPrompt(CODE_REVIEW_PROMPT, {
        review: codeReview.review,
        newActions: codeReview.newActions,
      }),
      cache_control: { type: "ephemeral" }
    });
  }

  return segments.filter(segment => segment.text.trim() !== "");
};

export async function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const model = await loadModel(config, Task.PROGRAMMER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    Task.PROGRAMMER,
  );
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
    ...state.internalMessages,
    ...missingMessages,
  ]);
  if (!inputMessages.length) {
    throw new Error("No messages to process.");
  }

  const response = await modelWithTools.invoke([
    {
      role: "system",
      content: formatPrompt({
        ...state,
        taskPlan: latestTaskPlan ?? state.taskPlan,
      }),
    },
    ...inputMessages,
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

