import { v4 as uuidv4 } from "uuid";
import {
  CustomRules,
  GraphConfig,
  GraphState,
  GraphUpdate,
  PlanItem,
  TaskPlan,
} from "@open-swe/shared/open-swe/types";
import {
  checkoutBranchAndCommit,
  getChangedFilesStatus,
  pushEmptyCommit,
} from "../../../utils/github/git.js";
import {
  createPullRequest,
  markPullRequestReadyForReview,
} from "../../../utils/github/api.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { z } from "zod";
import {
  loadModel,
  supportsParallelToolCallsParam,
} from "../../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { formatPlanPromptWithSummaries } from "../../../utils/plan-prompt.js";
import { formatUserRequestPrompt } from "../../../utils/user-request.js";
import { AIMessage, BaseMessage, ToolMessage } from "@langchain/core/messages";
import {
  deleteSandbox,
  getSandboxWithErrorHandling,
} from "../../../utils/sandbox.js";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import {
  getActivePlanItems,
  getPullRequestNumberFromActiveTask,
} from "@open-swe/shared/open-swe/tasks";
import { createOpenPrToolFields } from "@open-swe/shared/open-swe/tools";
import { trackCachePerformance } from "../../../utils/caching.js";
import { getModelManager } from "../../../utils/llms/model-manager.js";
import {
  GitHubPullRequest,
  GitHubPullRequestList,
  GitHubPullRequestUpdate,
} from "../../../utils/github/types.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";

const logger = createLogger(LogLevel.INFO, "Open PR");

const openPrSysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You have just completed all of your tasks, and are now ready to open a pull request.

Here are all of the tasks you completed:
{COMPLETED_TASKS}

{USER_REQUEST_PROMPT}

{CUSTOM_RULES}

Always use proper markdown formatting when generating the pull request contents.

You should not include any mention of an issue to close, unless explicitly requested by the user. The body will automatically include a mention of the issue to close.

With all of this in mind, please use the \`open_pr\` tool to open a pull request.`;

const formatCustomRulesPrompt = (pullRequestFormatting: string): string => {
  return `<custom_formatting_rules>
The user has provided the following custom rules around how to format the contents of the pull request.
IMPORTANT: You must follow these instructions exactly when generating the pull request contents. Do not deviate from them in any way.

${pullRequestFormatting}
</custom_formatting_rules>`;
};

const formatPrompt = (
  taskPlan: PlanItem[],
  messages: BaseMessage[],
  customRules?: CustomRules,
): string => {
  const completedTasks = taskPlan.filter((task) => task.completed);
  const customPrFormattingRules = customRules?.pullRequestFormatting
    ? formatCustomRulesPrompt(customRules.pullRequestFormatting)
    : "";
  return openPrSysPrompt
    .replace("{COMPLETED_TASKS}", formatPlanPromptWithSummaries(completedTasks))
    .replace("{USER_REQUEST_PROMPT}", formatUserRequestPrompt(messages))
    .replace("{CUSTOM_RULES}", customPrFormattingRules);
};

export async function openPullRequest(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const { githubInstallationToken } = getGitHubTokensFromConfig(config);

  const { sandbox, codebaseTree, dependenciesInstalled } =
    await getSandboxWithErrorHandling(
      state.sandboxSessionId,
      state.targetRepository,
      state.branchName,
      config,
    );
  const sandboxSessionId = sandbox.id;

  const { owner, repo } = state.targetRepository;

  if (!owner || !repo) {
    throw new Error(
      "Failed to open pull request: No target repository found in config.",
    );
  }

  let branchName = state.branchName;
  let updatedTaskPlan: TaskPlan | undefined;

  const repoPath = getRepoAbsolutePath(state.targetRepository);
  const changedFiles = await getChangedFilesStatus(repoPath, sandbox, config);

  if (changedFiles.length > 0) {
    logger.info(`Has ${changedFiles.length} changed files. Committing.`, {
      changedFiles,
    });
    const result = await checkoutBranchAndCommit(
      config,
      state.targetRepository,
      sandbox,
      {
        branchName,
        githubInstallationToken,
        taskPlan: state.taskPlan,
        githubIssueId: state.githubIssueId,
      },
    );
    branchName = result.branchName;
    updatedTaskPlan = result.updatedTaskPlan;
  }

  const openPrTool = createOpenPrToolFields();
  // use the router model since this is a simple task that doesn't need an advanced model
  const model = await loadModel(config, LLMTask.ROUTER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(config, LLMTask.ROUTER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.ROUTER,
  );
  const modelWithTool = model.bindTools([openPrTool], {
    tool_choice: openPrTool.name,
    ...(modelSupportsParallelToolCallsParam
      ? {
          parallel_tool_calls: false,
        }
      : {}),
  });

  const response = await modelWithTool.invoke([
    {
      role: "user",
      content: formatPrompt(
        getActivePlanItems(state.taskPlan),
        state.internalMessages,
      ),
    },
  ]);

  const toolCall = response.tool_calls?.[0];

  if (!toolCall) {
    throw new Error(
      "Failed to generate a tool call when opening a pull request.",
    );
  }

  if (process.env.SKIP_CI_UNTIL_LAST_COMMIT === "true") {
    await pushEmptyCommit(state.targetRepository, sandbox, config, {
      githubInstallationToken,
    });
  }

  const { title, body } = toolCall.args as z.infer<typeof openPrTool.schema>;

  const prForTask = getPullRequestNumberFromActiveTask(
    updatedTaskPlan ?? state.taskPlan,
  );
  let pullRequest:
    | GitHubPullRequest
    | GitHubPullRequestList[number]
    | GitHubPullRequestUpdate
    | null = null;
  if (!prForTask) {
    // No PR created yet. Shouldn't be possible, but we have a condition here anyway
    pullRequest = await createPullRequest({
      owner,
      repo,
      headBranch: branchName,
      title,
      body: `Fixes #${state.githubIssueId}\n\n${body}`,
      githubInstallationToken,
      baseBranch: state.targetRepository.branch,
    });
  } else {
    // Ensure the PR is ready for review
    pullRequest = await markPullRequestReadyForReview({
      owner,
      repo,
      title,
      body: `Fixes #${state.githubIssueId}\n\n${body}`,
      pullNumber: prForTask,
      githubInstallationToken,
    });
  }

  let sandboxDeleted = false;
  if (pullRequest) {
    // Delete the sandbox.
    sandboxDeleted = await deleteSandbox(sandboxSessionId);
  }

  const newMessages = [
    new AIMessage({
      ...response,
      additional_kwargs: {
        ...response.additional_kwargs,
        // Required for the UI to render these fields.
        branch: branchName,
        targetBranch: state.targetRepository.branch,
      },
    }),
    new ToolMessage({
      id: uuidv4(),
      tool_call_id: toolCall.id ?? "",
      content: pullRequest
        ? `Marked pull request as ready for review: ${pullRequest.html_url}`
        : "Failed to mark pull request as ready for review.",
      name: toolCall.name,
      additional_kwargs: {
        pull_request: pullRequest,
      },
    }),
  ];

  return {
    messages: newMessages,
    internalMessages: newMessages,
    // If the sandbox was successfully deleted, we can remove it from the state & reset the dependencies installed flag.
    ...(sandboxDeleted && {
      sandboxSessionId: undefined,
      dependenciesInstalled: false,
    }),
    ...(codebaseTree && { codebaseTree }),
    ...(dependenciesInstalled !== null && { dependenciesInstalled }),
    tokenData: trackCachePerformance(response, modelName),
    ...(updatedTaskPlan && { taskPlan: updatedTaskPlan }),
  };
}
