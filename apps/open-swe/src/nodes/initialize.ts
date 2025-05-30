import { Sandbox } from "@e2b/code-interpreter";
import { createLogger, LogLevel } from "../utils/logger.js";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
  TargetRepository,
} from "../types.js";
import { TIMEOUT_EXTENSION_OPT } from "../constants.js";
import {
  checkoutBranch,
  configureGitUserInRepo,
  getBranchName,
  getRepoAbsolutePath,
  pullLatestChanges,
} from "../utils/git/index.js";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";

const logger = createLogger(LogLevel.INFO, "Initialize");

const SANDBOX_TEMPLATE_ID = "eh0860emqx28qyxmbctu";

async function cloneRepo(sandbox: Sandbox, targetRepository: TargetRepository) {
  if (!process.env.GITHUB_PAT) {
    throw new Error("GITHUB_PAT environment variable not set.");
  }

  try {
    const gitCloneCommand = ["git", "clone"];

    const repoUrlWithToken = `https://${process.env.GITHUB_PAT}@github.com/${targetRepository.owner}/${targetRepository.repo}.git`;

    if (targetRepository.branch) {
      gitCloneCommand.push("-b", targetRepository.branch, repoUrlWithToken);
    } else {
      gitCloneCommand.push(repoUrlWithToken);
    }

    logger.info("Cloning repository", {
      command: gitCloneCommand.join(" "),
    });
    return await sandbox.commands.run(
      gitCloneCommand.join(" "),
      TIMEOUT_EXTENSION_OPT,
    );
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Failed to clone repository", errorFields ?? e);
    throw e;
  }
}

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
      const newSandbox = await Sandbox.resume(
        sandboxSessionId,
        TIMEOUT_EXTENSION_OPT,
      );
      await pullLatestChanges(absoluteRepoDir, newSandbox);
      return {
        sandboxSessionId: newSandbox.sandboxId,
      };
    } catch (e) {
      // Error thrown, log it and continue. Will create a new sandbox session since the resumption failed.
      logger.error("Failed to get sandbox session", e);
    }
  }

  logger.info("Creating sandbox...");
  const sandbox = await Sandbox.create(
    SANDBOX_TEMPLATE_ID,
    TIMEOUT_EXTENSION_OPT,
  );

  const res = await cloneRepo(sandbox, targetRepository);
  if (res.error) {
    // TODO: This should probably be an interrupt.
    logger.error("Failed to clone repository", res.error);
    throw new Error(`Failed to clone repository.\n${res.error}`);
  }
  logger.info("Repository cloned successfully.");


  logger.info(`Configuring git user for repository at "${absoluteRepoDir}"...`);
  await configureGitUserInRepo(absoluteRepoDir, sandbox);
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
    sandboxSessionId: sandbox.sandboxId,
    targetRepository,
  };
}
