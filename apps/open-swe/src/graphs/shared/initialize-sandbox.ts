import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getGitHubTokensFromConfig } from "../../utils/github-tokens.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { daytonaClient } from "../../utils/sandbox.js";
import {
  checkoutBranch,
  cloneRepo,
  configureGitUserInRepo,
  pullLatestChanges,
} from "../../utils/github/git.js";
import { getCodebaseTree } from "../../utils/tree.js";
import { SNAPSHOT_NAME } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "InitializeSandbox");

type InitializeSandboxState = {
  targetRepository: TargetRepository;
  branchName: string;
  sandboxSessionId?: string;
  codebaseTree?: string;
};

export async function initializeSandbox(
  state: InitializeSandboxState,
  config: GraphConfig,
): Promise<Partial<InitializeSandboxState>> {
  const { githubInstallationToken } = getGitHubTokensFromConfig(config);
  const { sandboxSessionId, targetRepository, branchName } = state;
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);

  if (sandboxSessionId) {
    try {
      logger.info("Sandbox session ID exists. Resuming", {
        sandboxSessionId,
      });
      // Resume the sandbox if the session ID is in the config.
      const existingSandbox = await daytonaClient().get(sandboxSessionId);
      await pullLatestChanges(absoluteRepoDir, existingSandbox);
      const codebaseTree = await getCodebaseTree(existingSandbox.id);
      return {
        sandboxSessionId: existingSandbox.id,
        codebaseTree,
      };
    } catch (e) {
      // Error thrown, log it and continue. Will create a new sandbox session since the resumption failed.
      logger.error("Failed to get sandbox session", e);
    }
  }

  logger.info("Creating sandbox...");
  const sandbox = await daytonaClient().create({
    image: SNAPSHOT_NAME,
  });

  const res = await cloneRepo(sandbox, targetRepository, {
    githubInstallationToken,
    stateBranchName: branchName,
  });
  if (res.exitCode !== 0) {
    throw new Error(`Failed to clone repository.\n${res.result}`);
  }
  
  logger.info("Repository cloned successfully.");

  logger.info(`Configuring git user for repository at "${absoluteRepoDir}"...`);
  await configureGitUserInRepo(absoluteRepoDir, sandbox, {
    githubInstallationToken,
    owner: targetRepository.owner,
    repo: targetRepository.repo,
  });
  logger.info("Git user configured successfully.");

  const checkoutBranchRes = await checkoutBranch(
    absoluteRepoDir,
    branchName,
    sandbox,
  );

  if (!checkoutBranchRes) {
    // TODO: This should probably be an interrupt.
    logger.error("Failed to checkout branch.");
    throw new Error("Failed to checkout branch");
  }

  const codebaseTree = await getCodebaseTree(sandbox.id);

  return {
    sandboxSessionId: sandbox.id,
    codebaseTree,
  };
}
