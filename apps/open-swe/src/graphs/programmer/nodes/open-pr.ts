import {
  GraphConfig,
  GraphState,
  GraphUpdate,
  PlanItem,
} from "@open-swe/shared/open-swe/types";
import {
  checkoutBranchAndCommit,
  getChangedFilesStatus,
} from "../../../utils/github/git.js";
import { createPullRequest } from "../../../utils/github/api.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { z } from "zod";
import { loadModel, Task } from "../../../utils/load-model.js";
import { formatPlanPromptWithSummaries } from "../../../utils/plan-prompt.js";
import { getUserRequest } from "../../../utils/user-request.js";
import { AIMessage, ToolMessage } from "@langchain/core/messages";
import {
  deleteSandbox,
  getSandboxWithErrorHandling,
} from "../../../utils/sandbox.js";
import { getGitHubTokensFromConfig } from "../../../utils/github-tokens.js";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { createOpenPrToolFields } from "@open-swe/shared/open-swe/tools";

const logger = createLogger(LogLevel.INFO, "Open PR");

const openPrSysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You have just completed all of your tasks, and are now ready to open a pull request.

Here are all of the tasks you completed:
{COMPLETED_TASKS}

And here is the user's original request:
{USER_REQUEST}

With all of this in mind, please use the \`open_pr\` tool to open a pull request.`;

const formatPrompt = (taskPlan: PlanItem[], userRequest: string): string => {
  const completedTasks = taskPlan.filter((task) => task.completed);
  return openPrSysPrompt
    .replace("{COMPLETED_TASKS}", formatPlanPromptWithSummaries(completedTasks))
    .replace("{USER_REQUEST}", userRequest);
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
  if (changedFiles.length > 0) {
    logger.info(`Has ${changedFiles.length} changed files. Committing.`, {
      changedFiles,
    });
    branchName = await checkoutBranchAndCommit(
      config,
      state.targetRepository,
      sandbox,
      {
        branchName,
      },
    );
  }

  const openPrTool = createOpenPrToolFields();
  const model = await loadModel(config, Task.SUMMARIZER);
  const modelWithTool = model.bindTools([openPrTool], {
    tool_choice: openPrTool.name,
    parallel_tool_calls: false,
  });

  const userRequest = getUserRequest(state.internalMessages);
  const response = await modelWithTool.invoke([
    {
      role: "user",
      content: formatPrompt(getActivePlanItems(state.taskPlan), userRequest),
    },
  ]);

  const toolCall = response.tool_calls?.[0];

  if (!toolCall) {
    throw new Error(
      "Failed to generate a tool call when opening a pull request.",
    );
  }

  const { title, body } = toolCall.args as z.infer<typeof openPrTool.schema>;

  const pr = await createPullRequest({
    owner,
    repo,
    headBranch: branchName,
    title,
    body: `Fixes #${state.githubIssueId}\n\n${body}`,
    githubInstallationToken,
    baseBranch: state.targetRepository.branch,
  });

  let sandboxDeleted = false;
  if (pr) {
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
      tool_call_id: toolCall.id ?? "",
      content: pr
        ? `Created pull request: ${pr.html_url}`
        : "Failed to create pull request.",
      name: toolCall.name,
      additional_kwargs: {
        pull_request: pr,
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
  };
}
