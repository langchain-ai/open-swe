import {
  ReviewerGraphState,
  ReviewerGraphUpdate,
} from "@open-swe/shared/open-swe/reviewer/types";
import { daytonaClient } from "../../../utils/sandbox.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";

export async function initializeState(
  state: ReviewerGraphState,
): Promise<ReviewerGraphUpdate> {
  const repoRoot = getRepoAbsolutePath(state.targetRepository);
  // get the head branch name, then get the changed files
  const sandbox = await daytonaClient().get(state.sandboxSessionId);

  const headBranchNameRes = await sandbox.process.executeCommand(
    "git rev-parse --abbrev-ref HEAD",
    repoRoot,
  );
  if (headBranchNameRes.exitCode !== 0) {
    throw new Error(
      `Failed to get head branch name: ${JSON.stringify(headBranchNameRes, null, 2)}`,
    );
  }
  const headBranchName = headBranchNameRes.result.trim();

  const changedFilesRes = await sandbox.process.executeCommand(
    `git diff ${headBranchName} --name-only`,
    repoRoot,
  );
  if (changedFilesRes.exitCode !== 0) {
    throw new Error(
      `Failed to get changed files: ${JSON.stringify(changedFilesRes, null, 2)}`,
    );
  }
  const changedFiles = changedFilesRes.result.trim();

  const codebaseTreeRes = await sandbox.process.executeCommand(
    "git ls-files | tree --fromfile -L 3",
    repoRoot,
  );
  if (codebaseTreeRes.exitCode !== 0) {
    throw new Error(
      `Failed to get codebase tree: ${JSON.stringify(codebaseTreeRes, null, 2)}`,
    );
  }
  const codebaseTree = codebaseTreeRes.result.trim();

  return {
    headBranchName,
    changedFiles,
    codebaseTree,
  };
}
