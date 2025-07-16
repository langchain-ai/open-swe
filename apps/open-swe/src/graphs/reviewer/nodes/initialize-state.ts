import {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@open-swe/shared/open-swe/reviewer/types";
import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { AIMessage, ToolMessage } from "@langchain/core/messages";
import { v4 as uuidv4 } from "uuid";
import { createReviewStartedToolFields } from "@open-swe/shared/open-swe/tools";
import { getSandboxErrorFields } from "../../../utils/sandbox-error-fields.js";
import { Sandbox } from "@daytonaio/sdk";

const logger = createLogger(LogLevel.INFO, "InitializeStateNode");

function createReviewStartedMessage() {
  const reviewStartedTool = createReviewStartedToolFields();
  const toolCallId = uuidv4();
  const reviewStartedToolCall = {
    id: toolCallId,
    name: reviewStartedTool.name,
    args: {
      review_started: true,
    },
  };

  return [
    new AIMessage({
      id: uuidv4(),
      content: "",
      additional_kwargs: {
        hidden: true,
      },
      tool_calls: [reviewStartedToolCall],
    }),
    new ToolMessage({
      id: uuidv4(),
      tool_call_id: toolCallId,
      content: "Review started",
      additional_kwargs: {
        hidden: true,
      },
    }),
  ];
}

async function getChangedFiles(
  sandbox: Sandbox,
  baseBranchName: string,
  repoRoot: string,
): Promise<string> {
  try {
    const changedFilesRes = await sandbox.process.executeCommand(
      `git diff ${baseBranchName} --name-only`,
      repoRoot,
    );
    if (changedFilesRes.exitCode !== 0) {
      const errorFields = getSandboxErrorFields(changedFilesRes);
      logger.error(
        `Failed to get changed files: ${JSON.stringify(errorFields, null, 2)}`,
      );
    }
    return changedFilesRes.result.trim();
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Failed to get changed files.", {
      ...(errorFields ? { errorFields } : { e }),
    });
    return "Failed to get changed files.";
  }
}

async function getBaseBranchName(sandbox: Sandbox, repoRoot: string): Promise<string> {
  try {
    const baseBranchNameRes = await sandbox.process.executeCommand(
      "git config init.defaultBranch",
      repoRoot,
    );
    if (baseBranchNameRes.exitCode !== 0) {
      const errorFields = getSandboxErrorFields(baseBranchNameRes);
      logger.error(
        "Failed to get base branch name",
        {
          ...(errorFields ?? baseBranchNameRes)
        }
      );
      return "";
    }
    return baseBranchNameRes.result.trim();
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Failed to get base branch name.", {
      ...(errorFields ? { errorFields } : { e }),
    });
    return "";
  }
}

export async function initializeState(
  state: ReviewerGraphState,
  config: GraphConfig,
): Promise<ReviewerGraphUpdate> {
  const repoRoot = getRepoAbsolutePath(state.targetRepository);
  logger.info("Initializing state for reviewer");
  // get the base branch name, then get the changed files
  const { sandbox, codebaseTree, dependenciesInstalled } =
    await getSandboxWithErrorHandling(
      state.sandboxSessionId,
      state.targetRepository,
      state.branchName,
      config,
    );

  let baseBranchName = state.targetRepository.branch;
  if (!baseBranchName) {
    baseBranchName = await getBaseBranchName(sandbox, repoRoot);
  }
  const changedFiles = baseBranchName ? await getChangedFiles(sandbox, baseBranchName, repoRoot) : "";

  logger.info("Finished getting state for reviewer");

  return {
    baseBranchName,
    changedFiles,
    messages: createReviewStartedMessage(),
    ...(codebaseTree ? { codebaseTree } : {}),
    ...(dependenciesInstalled !== null ? { dependenciesInstalled } : {}),
  };
}
