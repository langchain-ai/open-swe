import {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@open-swe/shared/open-swe/reviewer/types";
import { getSandboxWithErrorHandling } from "../../../utils/sandbox.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

const logger = createLogger(LogLevel.INFO, "InitializeStateNode");

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
    const baseBranchNameRes = await sandbox.process.executeCommand(
      "git config init.defaultBranch",
      repoRoot,
    );
    if (baseBranchNameRes.exitCode !== 0) {
      throw new Error(
        `Failed to get base branch name: ${JSON.stringify(baseBranchNameRes, null, 2)}`,
      );
    }
    baseBranchName = baseBranchNameRes.result.trim();
  }

  const changedFilesRes = await sandbox.process.executeCommand(
    `git diff ${baseBranchName} --name-only`,
    repoRoot,
  );
  if (changedFilesRes.exitCode !== 0) {
    throw new Error(
      `Failed to get changed files: ${JSON.stringify(changedFilesRes, null, 2)}`,
    );
  }
  const changedFiles = changedFilesRes.result.trim();

  logger.info("Finished getting state for reviewer");

  return {
    baseBranchName,
    changedFiles,
    ...(codebaseTree ? { codebaseTree } : {}),
    ...(dependenciesInstalled !== null ? { dependenciesInstalled } : {}),
  };
}
