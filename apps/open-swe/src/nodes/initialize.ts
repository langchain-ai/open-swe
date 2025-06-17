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
  const branchName = state.branchName || getBranchName(config);
  const repoName = `${targetRepository.owner}/${targetRepository.repo}`;

  // Helper to emit events and log errors in dev
  function emitEvent(event: CustomEvent) {
    try {
      config.writer?.(event);
    } catch (err) {
      // TODO remove after dev
      logger.error("[DEV] Failed to emit custom event", { event, err });
    }
  }

  if (sandboxSessionId) {
    try {
      // Resuming Sandbox
      const resumeSandboxActionId = uuidv4();
      const baseResumeSandboxAction: CustomEvent = {
        nodeId: INITIALIZE_NODE_ID,
        createdAt: new Date().toISOString(),
        actionId: resumeSandboxActionId,
        action: "Resuming Sandbox",
        data: {
          status: "pending",
          sandboxSessionId,
          branch: branchName,
          repo: repoName,
        }
      };
      emitEvent(baseResumeSandboxAction);
      try {
        const existingSandbox = await daytonaClient().get(sandboxSessionId);
        emitEvent({
          ...baseResumeSandboxAction,
          createdAt: new Date().toISOString(),
          data: { ...baseResumeSandboxAction.data, status: "success" },
        });
        // Pulling latest changes
        const pullLatestChangesActionId = uuidv4();
        const basePullLatestChangesAction: CustomEvent = {
          nodeId: INITIALIZE_NODE_ID,
          createdAt: new Date().toISOString(),
          actionId: pullLatestChangesActionId,
          action: "Pulling latest changes",
          data: {
            status: "pending",
            sandboxSessionId,
            branch: branchName,
            repo: repoName,
          }
        };
        emitEvent(basePullLatestChangesAction);
        try {
          await pullLatestChanges(absoluteRepoDir, existingSandbox);
          emitEvent({
            ...basePullLatestChangesAction,
            createdAt: new Date().toISOString(),
            data: { ...basePullLatestChangesAction.data, status: "success" },
          });
        } catch (_) {
          emitEvent({
            ...basePullLatestChangesAction,
            createdAt: new Date().toISOString(),
            data: {
              ...basePullLatestChangesAction.data,
              status: "error",
              error: "Failed to pull latest changes. Please check your repository connection.",
            },
          });
        }
        // Generating codebase tree
        const generateCodebaseTreeActionId = uuidv4();
        const baseGenerateCodebaseTreeAction: CustomEvent = {
          nodeId: INITIALIZE_NODE_ID,
          createdAt: new Date().toISOString(),
          actionId: generateCodebaseTreeActionId,
          action: "Generating codebase tree",
          data: {
            status: "pending",
            sandboxSessionId,
            branch: branchName,
            repo: repoName,
          }
        };
        emitEvent(baseGenerateCodebaseTreeAction);
        try {
          const codebaseTree = await getCodebaseTree(existingSandbox.id);
          emitEvent({
            ...baseGenerateCodebaseTreeAction,
            createdAt: new Date().toISOString(),
            data: { ...baseGenerateCodebaseTreeAction.data, status: "success" },
          });
          return {
            sandboxSessionId: existingSandbox.id,
            codebaseTree,
          };
        } catch (_) {
          emitEvent({
            ...baseGenerateCodebaseTreeAction,
            createdAt: new Date().toISOString(),
            data: {
              ...baseGenerateCodebaseTreeAction.data,
              status: "error",
              error: "Failed to generate codebase tree. Please try again later.",
            },
          });
        }
      } catch (_) {
        emitEvent({
          ...baseResumeSandboxAction,
          createdAt: new Date().toISOString(),
          data: {
            ...baseResumeSandboxAction.data,
            status: "error",
            error: "Failed to resume sandbox. A new environment will be created.",
          },
        });
      }
    } catch (_) {
      // TODO remove after dev
      logger.error("[DEV] Failed to get sandbox session", _);
    }
  }

  // Creating Sandbox
  const createSandboxActionId = uuidv4();
  const baseCreateSandboxAction: CustomEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: createSandboxActionId,
    action: "Creating Sandbox",
    data: {
      status: "pending",
      sandboxSessionId: null,
      branch: branchName,
      repo: repoName,
    }
  };
  emitEvent(baseCreateSandboxAction);
  let sandbox;
  try {
    sandbox = await daytonaClient().create({ image: SNAPSHOT_NAME });
    emitEvent({
      ...baseCreateSandboxAction,
      createdAt: new Date().toISOString(),
      data: { ...baseCreateSandboxAction.data, status: "success", sandboxSessionId: sandbox.id },
    });
  } catch (_) {
    emitEvent({
      ...baseCreateSandboxAction,
      createdAt: new Date().toISOString(),
      data: {
        ...baseCreateSandboxAction.data,
        status: "error",
        error: "Failed to create sandbox environment. Please try again later.",
      },
    });
    // TODO remove after dev
    logger.error("[DEV] Failed to create sandbox", _);
    return {};
  }

  // Cloning repository
  const cloneRepoActionId = uuidv4();
  const baseCloneRepoAction: CustomEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: cloneRepoActionId,
    action: "Cloning repository",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    }
  };
  emitEvent(baseCloneRepoAction);
  let res;
  try {
    res = await cloneRepo(sandbox, targetRepository, {
      githubInstallationToken,
      stateBranchName: state.branchName,
    });
    if (res.exitCode !== 0) throw new Error();
    emitEvent({
      ...baseCloneRepoAction,
      createdAt: new Date().toISOString(),
      data: { ...baseCloneRepoAction.data, status: "success" },
    });
  } catch (_) {
    emitEvent({
      ...baseCloneRepoAction,
      createdAt: new Date().toISOString(),
      data: {
        ...baseCloneRepoAction.data,
        status: "error",
        error: "Failed to clone repository. Please check your repo URL and permissions.",
      },
    });
    // TODO remove after dev
    logger.error("[DEV] Failed to clone repository", res?.result || _);
  }

  // Configuring git user
  const configureGitUserActionId = uuidv4();
  const baseConfigureGitUserAction: CustomEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: configureGitUserActionId,
    action: "Configuring git user",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    }
  };
  emitEvent(baseConfigureGitUserAction);
  try {
    await configureGitUserInRepo(absoluteRepoDir, sandbox, {
      githubInstallationToken,
      owner: targetRepository.owner,
      repo: targetRepository.repo,
    });
    emitEvent({
      ...baseConfigureGitUserAction,
      createdAt: new Date().toISOString(),
      data: { ...baseConfigureGitUserAction.data, status: "success" },
    });
  } catch (_) {
    emitEvent({
      ...baseConfigureGitUserAction,
      createdAt: new Date().toISOString(),
      data: {
        ...baseConfigureGitUserAction.data,
        status: "error",
        error: "Failed to configure git user. Please check your git settings.",
      },
    });
    // TODO remove after dev
    logger.error("[DEV] Failed to configure git user", _);
  }

  // Checking out branch
  const checkoutBranchActionId = uuidv4();
  const baseCheckoutBranchAction: CustomEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: checkoutBranchActionId,
    action: "Checking out branch",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    }
  };
  emitEvent(baseCheckoutBranchAction);
  try {
    const checkoutBranchRes = await checkoutBranch(
      absoluteRepoDir,
      branchName,
      sandbox,
    );
    if (!checkoutBranchRes) throw new Error();
    emitEvent({
      ...baseCheckoutBranchAction,
      createdAt: new Date().toISOString(),
      data: { ...baseCheckoutBranchAction.data, status: "success" },
    });
  } catch (_) {
    emitEvent({
      ...baseCheckoutBranchAction,
      createdAt: new Date().toISOString(),
      data: {
        ...baseCheckoutBranchAction.data,
        status: "error",
        error: "Failed to checkout branch. Please check your branch name.",
      },
    });
    // TODO remove after dev
    logger.error("[DEV] Failed to checkout branch", _);
  }

  // Generating codebase tree
  const generateCodebaseTreeActionId = uuidv4();
  const baseGenerateCodebaseTreeAction: CustomEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: generateCodebaseTreeActionId,
    action: "Generating codebase tree",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    }
  };
  emitEvent(baseGenerateCodebaseTreeAction);
  let codebaseTree = undefined;
  try {
    codebaseTree = await getCodebaseTree(sandbox.id);
    emitEvent({
      ...baseGenerateCodebaseTreeAction,
      createdAt: new Date().toISOString(),
      data: { ...baseGenerateCodebaseTreeAction.data, status: "success" },
    });
  } catch (_) {
    emitEvent({
      ...baseGenerateCodebaseTreeAction,
      createdAt: new Date().toISOString(),
      data: {
        ...baseGenerateCodebaseTreeAction.data,
        status: "error",
        error: "Failed to generate codebase tree. Please try again later.",
      },
    });
    // TODO remove after dev
    logger.error("[DEV] Failed to generate codebase tree", _);
  }

  return {
    sandboxSessionId: sandbox?.id,
    targetRepository,
    codebaseTree,
  };
}
