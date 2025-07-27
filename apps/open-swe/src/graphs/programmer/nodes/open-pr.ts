import { v4 as uuidv4 } from "uuid";
import {
  GraphConfig,
  GraphState,
  GraphUpdate,
  PlanItem,
  TaskPlan,
} from "@open-swe/shared/open-swe/types";
import {
  checkoutBranchAndCommit,
  getChangedFilesStatus,
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
  Task,
} from "../../../utils/llms/index.js";
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
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { createOpenPrToolFields } from "@open-swe/shared/open-swe/tools";
import { trackCachePerformance } from "../../../utils/caching.js";
import { getModelManager } from "../../../utils/llms/model-manager.js";
import {
  GitHubPullRequest,
  GitHubPullRequestList,
  GitHubPullRequestUpdate,
} from "../../../utils/github/types.js";

const logger = createLogger(LogLevel.INFO, "Open PR");

const openPrSysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You have just completed all of your tasks, and are now ready to open a pull request.

Here are all of the tasks you completed:
{COMPLETED_TASKS}

{USER_REQUEST_PROMPT}

With all of this in mind, please use the \`open_pr\` tool to open a pull request.`;

const formatPrompt = (
  taskPlan: PlanItem[],
  messages: BaseMessage[],
): string => {
  const completedTasks = taskPlan.filter((task) => task.completed);
  return openPrSysPrompt
    .replace("{COMPLETED_TASKS}", formatPlanPromptWithSummaries(completedTasks))
    .replace("{USER_REQUEST_PROMPT}", formatUserRequestPrompt(messages));
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

  const changedFiles = await getChangedFilesStatus(
    getRepoAbsolutePath(state.targetRepository),
    sandbox,
  );
  let branchName = state.branchName;
  let updatedTaskPlan: TaskPlan | undefined;
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
  const model = await loadModel(config, Task.ROUTER);
  const modelManager = getModelManager();
  const modelName = modelManager.getModelNameForTask(config, Task.ROUTER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    Task.ROUTER,
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
