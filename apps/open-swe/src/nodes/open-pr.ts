import { GraphConfig, GraphState, GraphUpdate, PlanItem } from "../types.js";
import {
  checkoutBranchAndCommit,
  createPullRequest,
  getBranchName,
  getChangedFilesStatus,
  getRepoAbsolutePath,
} from "../utils/git.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { z } from "zod";
import { loadModel, Task } from "../utils/load-model.js";
import { formatPlanPromptWithSummaries } from "../utils/plan-prompt.js";
import { getUserRequest } from "../utils/user-request.js";
import { ToolMessage } from "@langchain/core/messages";
import { daytonaClient, deleteSandbox } from "../utils/sandbox.js";
import { getGitHubTokensFromConfig } from "../utils/github-tokens.js";
import { getActivePlanItems } from "../utils/task-plan.js";

const logger = createLogger(LogLevel.INFO, "Open PR");

const openPrSysPrompt = `You are operating as a terminal-based agentic coding assistant built by LangChain. It wraps LLM models to enable natural language interaction with a local codebase. You are expected to be precise, safe, and helpful.

You have just completed all of your tasks, and are now ready to open a pull request.

Here are all of the tasks you completed:
{COMPLETED_TASKS}

And here is the user's original request:
{USER_REQUEST}

With all of this in mind, please use the \`open_pr\` tool to open a pull request.`;

const openPrToolSchema = z.object({
  title: z
    .string()
    .describe(
      "The title of the pull request. Ensure this is a concise and thoughtful title. You should follow conventional commit title format (e.g. prefixing with 'fix:', 'feat:', 'chore:', etc.).",
    ),
  body: z
    .string()
    .optional()
    .describe(
      "The body of the pull request. This should provide a detailed description of the changes you've made, and why you've made them. Ensure you do not over-explain the changes, as we do not want to waste the user's time.",
    ),
});

const openPrTool = {
  name: "open_pr",
  schema: openPrToolSchema,
  description: "Use this tool to open a pull request.",
};

const formatPrompt = (plan: PlanItem[], userRequest: string): string => {
  const completedTasks = plan.filter((task) => task.completed);
  return openPrSysPrompt
    .replace("{COMPLETED_TASKS}", formatPlanPromptWithSummaries(completedTasks))
    .replace("{USER_REQUEST}", userRequest);
};

export async function openPullRequest(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  const sandboxSessionId = state.sandboxSessionId;
  if (!sandboxSessionId) {
    throw new Error(
      "Failed to open pull request: No sandbox session ID found in state.",
    );
  }
  const { githubToken } = getGitHubTokensFromConfig(config);

  const sandbox = await daytonaClient().get(sandboxSessionId);

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

  const model = await loadModel(config, Task.SUMMARIZER);
  const modelWithTool = model.bindTools([openPrTool], {
    tool_choice: openPrTool.name,
  });

  const userRequest = getUserRequest(state.messages);
  const response = await modelWithTool.invoke([
    {
      role: "user",
      content: formatPrompt(getActivePlanItems(state.plan), userRequest),
    },
  ]);

  const toolCall = response.tool_calls?.[0];

  if (!toolCall) {
    throw new Error(
      "Failed to generate a tool call when opening a pull request.",
    );
  }

  const { title, body } = toolCall.args as z.infer<typeof openPrToolSchema>;

  const pr = await createPullRequest({
    owner,
    repo,
    headBranch: branchName ?? getBranchName(config),
    title,
    body,
    githubToken,
  });

  let sandboxDeleted = false;
  if (pr) {
    // Delete the sandbox.
    sandboxDeleted = await deleteSandbox(sandboxSessionId);
  }

  return {
    messages: [
      response,
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
    ],
    // If the sandbox was successfully deleted, we can remove it from the state.
    ...(sandboxDeleted && { sandboxSessionId: undefined }),
  };
}
