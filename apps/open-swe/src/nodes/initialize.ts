import { createLogger, LogLevel } from "../utils/logger.js";
import { GraphState, GraphConfig, GraphUpdate } from "../types.js";
import {
  checkoutBranch,
  cloneRepo,
  configureGitUserInRepo,
  getBranchName,
  getRepoAbsolutePath,
  pullLatestChanges,
} from "../utils/git.js";
import { daytonaClient } from "../utils/sandbox.js";
import { SNAPSHOT_NAME } from "../constants.js";

const logger = createLogger(LogLevel.INFO, "Initialize");

/**
 * Initializes the session. This ensures there's an active VM session, and that
 * the proper credentials are provided for taking actions on GitHub.
 * It also clones the repository the user has specified to be used, and an optional
 * branch.
 */
export async function initialize(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  if (!config.configurable) {
    throw new Error("Configuration object not found.");
  }
  const githubToken = config.configurable["x-github-installation-token"];
  const githubAccessToken = config.configurable["x-github-access-token"];
  if (!githubToken) {
    throw new Error(
      "Missing required x-github-installation-token in configuration.",
    );
  }
  if (!githubAccessToken) {
    throw new Error("Missing required x-github-access-token in configuration.");
  }
  const { sandboxSessionId } = state;
  const { targetRepository } = state;
  if (!targetRepository) {
    throw new Error(
      "Missing required target repository. Please provide a git repository in state or configuration.",
    );
  }
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);

  if (sandboxSessionId) {
    try {
      logger.info("Sandbox session ID exists. Resuming", {
        sandboxSessionId,
      });
      // Resume the sandbox if the session ID is in the config.
      const existingSandbox = await daytonaClient().get(sandboxSessionId);
      await pullLatestChanges(absoluteRepoDir, existingSandbox);
      return {
        sandboxSessionId: existingSandbox.id,
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

  const res = await cloneRepo(sandbox, targetRepository, { githubToken });
  if (res.exitCode !== 0) {
    // TODO: This should probably be an interrupt.
    logger.error("Failed to clone repository", res.result);
    throw new Error(`Failed to clone repository.\n${res.result}`);
  }
  logger.info("Repository cloned successfully.");

  logger.info(`Configuring git user for repository at "${absoluteRepoDir}"...`);
  await configureGitUserInRepo(absoluteRepoDir, sandbox, {
    githubToken,
    githubAccessToken,
    owner: targetRepository.owner,
    repo: targetRepository.repo,
  });
  logger.info("Git user configured successfully.");

  const checkoutBranchRes = await checkoutBranch(
    absoluteRepoDir,
    state.branchName || getBranchName(config),
    sandbox,
  );

  if (!checkoutBranchRes) {
    // TODO: This should probably be an interrupt.
    logger.error("Failed to checkout branch.");
    throw new Error("Failed to checkout branch");
  }

  return {
    sandboxSessionId: sandbox.id,
    targetRepository,
  };
}
