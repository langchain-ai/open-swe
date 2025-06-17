import { v4 as uuidv4 } from "uuid";
import { createLogger, LogLevel } from "../utils/logger.js";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
} from "@open-swe/shared/open-swe/types";
import {
  checkoutBranch,
  cloneRepo,
  configureGitUserInRepo,
  getBranchName,
  pullLatestChanges,
} from "../utils/git.js";
import { daytonaClient } from "../utils/sandbox.js";
import { SNAPSHOT_NAME } from "@open-swe/shared/constants";
import { getGitHubTokensFromConfig } from "../utils/github-tokens.js";
import { getCodebaseTree } from "../utils/tree.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { CustomEvent, INITIALIZE_NODE_ID } from "@open-swe/shared/open-swe/custom-events";

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
  const { githubInstallationToken } = getGitHubTokensFromConfig(config);
  const { sandboxSessionId, targetRepository } = state;
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);

  if (sandboxSessionId) {
    try {
      logger.info("Sandbox session ID exists. Resuming", {
        sandboxSessionId,
      });
      const resumeSandboxActionId = uuidv4();
      config.writer?.({
        nodeId: INITIALIZE_NODE_ID,
        createdAt: new Date().toISOString(),
        actionId: resumeSandboxActionId,
        action: "Resuming Sandbox",
        data: {
          status: "pending",
          sandboxSessionId,
        }
      });

      // Resume the sandbox if the session ID is in the config.
      const existingSandbox = await daytonaClient().get(sandboxSessionId);
      config.writer?.({
        nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
        actionId: resumeSandboxActionId,
        action: "Resuming Sandbox",
        data: {
          status: "success",
          sandboxSessionId,
        }
      });
      const pullLatestChangesActionId = uuidv4();
      config.writer?.({
        nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
        actionId: pullLatestChangesActionId,
        action: "Pulling latest changes",
        data: {
          status: "pending",
          sandboxSessionId,
        }
      });
      await pullLatestChanges(absoluteRepoDir, existingSandbox);
      config.writer?.({
        nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
        actionId: pullLatestChangesActionId,
        action: "Pulling latest changes",
        data: {
          status: "success",
          sandboxSessionId,
        }
      });

      const generateCodebaseTreeActionId = uuidv4();
      config.writer?.({
        nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
        actionId: generateCodebaseTreeActionId,
        action: "Generating codebase tree",
        data: {
          status: "pending",
          sandboxSessionId,
        }
      });
      const codebaseTree = await getCodebaseTree(existingSandbox.id);
      config.writer?.({
        nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
        actionId: generateCodebaseTreeActionId,
        action: "Generating codebase tree",
        data: {
          status: "success",
          sandboxSessionId,
        }
      });
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
  const createSandboxActionId = uuidv4();
  // TODO: Update the above .write() calls to use this pattern.
  const baseCreateSandboxAction: CustomEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: createSandboxActionId,
    action: "Creating Sandbox",
    data: {
      status: "pending",
      sandboxSessionId: null,
    }
  }
  config.writer?.(baseCreateSandboxAction);

  const sandbox = await daytonaClient().create({
    image: SNAPSHOT_NAME,
  });

  config.writer?.({
    ...baseCreateSandboxAction,
    createdAt: new Date().toISOString(),
    data: {
      status: "success",
      sandboxSessionId: sandbox.id,
    }
  });

  const res = await cloneRepo(sandbox, targetRepository, {
    githubInstallationToken,
    stateBranchName: state.branchName,
  });
  if (res.exitCode !== 0) {
    // TODO: This should probably be an interrupt.
    logger.error("Failed to clone repository", res.result);
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
    state.branchName || getBranchName(config),
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
    targetRepository,
    codebaseTree,
  };
}
