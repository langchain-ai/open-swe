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
import {
  CustomEvent,
  INITIALIZE_NODE_ID,
} from "@open-swe/shared/open-swe/custom-events";

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

  function emitEvent(event: CustomEvent) {
    try {
      config.writer?.(event);
    } catch (err) {
      // TODO remove after dev
      logger.error("[DEV] Failed to emit custom event", { event, err });
    }
  }

  // Helper function to emit step events
  function emitStepEvent(
    base: CustomEvent,
    status: "pending" | "success" | "error" | "skipped",
    error?: string,
  ) {
    emitEvent({
      ...base,
      createdAt: new Date().toISOString(),
      data: {
        ...base.data,
        status,
        ...(error ? { error } : {}),
      },
    });
  }

  if (!sandboxSessionId) {
    emitStepEvent(
      {
        nodeId: INITIALIZE_NODE_ID,
        createdAt: new Date().toISOString(),
        actionId: uuidv4(),
        action: "Resuming Sandbox",
        data: {
          status: "skipped",
          branch: branchName,
          repo: repoName,
        },
      },
      "skipped",
    );
    emitStepEvent(
      {
        nodeId: INITIALIZE_NODE_ID,
        createdAt: new Date().toISOString(),
        actionId: uuidv4(),
        action: "Pulling latest changes",
        data: {
          status: "skipped",
          branch: branchName,
          repo: repoName,
        },
      },
      "skipped",
    );
  }

  if (sandboxSessionId) {
    try {
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
        },
      };
      emitStepEvent(baseResumeSandboxAction, "pending");
      try {
        const existingSandbox = await daytonaClient().get(sandboxSessionId);
        emitStepEvent(baseResumeSandboxAction, "success");
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
          },
        };
        emitStepEvent(basePullLatestChangesAction, "pending");
        try {
          await pullLatestChanges(absoluteRepoDir, existingSandbox);
          emitStepEvent(basePullLatestChangesAction, "success");
        } catch (_) {
          emitStepEvent(
            basePullLatestChangesAction,
            "error",
            "Failed to pull latest changes. Please check your repository connection.",
          );
        }
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
          },
        };
        emitStepEvent(baseGenerateCodebaseTreeAction, "pending");
        try {
          const codebaseTree = await getCodebaseTree(existingSandbox.id);
          emitStepEvent(baseGenerateCodebaseTreeAction, "success");
          return {
            sandboxSessionId: existingSandbox.id,
            codebaseTree,
          };
        } catch (_) {
          emitStepEvent(
            baseGenerateCodebaseTreeAction,
            "error",
            "Failed to generate codebase tree. Please try again later.",
          );
        }
      } catch (_) {
        emitStepEvent(
          baseResumeSandboxAction,
          "error",
          "Failed to resume sandbox. A new environment will be created.",
        );
      }
    } catch (_) {
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
    },
  };
  emitStepEvent(baseCreateSandboxAction, "pending");
  let sandbox;
  try {
    sandbox = await daytonaClient().create({ image: SNAPSHOT_NAME });
    emitStepEvent(baseCreateSandboxAction, "success");
  } catch (_) {
    emitStepEvent(
      baseCreateSandboxAction,
      "error",
      "Failed to create sandbox environment. Please try again later.",
    );
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
    },
  };
  emitStepEvent(baseCloneRepoAction, "pending");
  let res;
  try {
    res = await cloneRepo(sandbox, targetRepository, {
      githubInstallationToken,
      stateBranchName: state.branchName,
    });
    if (res.exitCode !== 0) throw new Error();
    emitStepEvent(baseCloneRepoAction, "success");
  } catch (_) {
    emitStepEvent(
      baseCloneRepoAction,
      "error",
      "Failed to clone repository. Please check your repo URL and permissions.",
    );
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
    },
  };
  emitStepEvent(baseConfigureGitUserAction, "pending");
  try {
    await configureGitUserInRepo(absoluteRepoDir, sandbox, {
      githubInstallationToken,
      owner: targetRepository.owner,
      repo: targetRepository.repo,
    });
    emitStepEvent(baseConfigureGitUserAction, "success");
  } catch (_) {
    emitStepEvent(
      baseConfigureGitUserAction,
      "error",
      "Failed to configure git user. Please check your git settings.",
    );
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
    },
  };
  emitStepEvent(baseCheckoutBranchAction, "pending");
  try {
    const checkoutBranchRes = await checkoutBranch(
      absoluteRepoDir,
      branchName,
      sandbox,
    );
    if (!checkoutBranchRes) throw new Error();
    emitStepEvent(baseCheckoutBranchAction, "success");
  } catch (_) {
    emitStepEvent(
      baseCheckoutBranchAction,
      "error",
      "Failed to checkout branch. Please check your branch name.",
    );
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
    },
  };
  emitStepEvent(baseGenerateCodebaseTreeAction, "pending");
  let codebaseTree = undefined;
  try {
    codebaseTree = await getCodebaseTree(sandbox.id);
    emitStepEvent(baseGenerateCodebaseTreeAction, "success");
  } catch (_) {
    emitStepEvent(
      baseGenerateCodebaseTreeAction,
      "error",
      "Failed to generate codebase tree. Please try again later.",
    );
  }

  return {
    sandboxSessionId: sandbox?.id,
    targetRepository,
    codebaseTree,
  };
}
