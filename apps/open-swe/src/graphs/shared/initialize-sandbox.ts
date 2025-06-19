import { v4 as uuidv4 } from "uuid";
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
import {
  DO_NOT_RENDER_ID_PREFIX,
  SNAPSHOT_NAME,
} from "@open-swe/shared/constants";
import {
  CustomNodeEvent,
  INITIALIZE_NODE_ID,
} from "@open-swe/shared/open-swe/custom-node-events";
import { Sandbox } from "@daytonaio/sdk";
import { AIMessage, BaseMessage } from "@langchain/core/messages";

const logger = createLogger(LogLevel.INFO, "InitializeSandbox");

type InitializeSandboxState = {
  targetRepository: TargetRepository;
  branchName: string;
  sandboxSessionId?: string;
  codebaseTree?: string;
  messages?: BaseMessage[];
};

export async function initializeSandbox(
  state: InitializeSandboxState,
  config: GraphConfig,
): Promise<Partial<InitializeSandboxState>> {
  const { githubInstallationToken } = getGitHubTokensFromConfig(config);
  const { sandboxSessionId, targetRepository, branchName } = state;
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  const repoName = `${targetRepository.owner}/${targetRepository.repo}`;

  const events: CustomNodeEvent[] = [];
  const emitStepEvent = (
    base: CustomNodeEvent,
    status: "pending" | "success" | "error" | "skipped",
    error?: string,
  ) => {
    const event = {
      ...base,
      createdAt: new Date().toISOString(),
      data: {
        ...base.data,
        status,
        ...(error ? { error } : {}),
      },
    };
    events.push(event);
    try {
      config.writer?.(event);
    } catch (err) {
      logger.error("Failed to emit custom event", { event, err });
    }
  };
  const createEventsMessage = () => [
    new AIMessage({
      id: `${DO_NOT_RENDER_ID_PREFIX}${uuidv4()}`,
      content: "",
      additional_kwargs: {
        hidden: true,
        customNodeEvents: events,
      },
    }),
  ];

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
    const resumeSandboxActionId = uuidv4();
    const baseResumeSandboxAction: CustomNodeEvent = {
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
      const basePullLatestChangesAction: CustomNodeEvent = {
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

      const pullChangesRes = await pullLatestChanges(
        absoluteRepoDir,
        existingSandbox,
      );
      if (!pullChangesRes || pullChangesRes.exitCode !== 0) {
        emitStepEvent(
          basePullLatestChangesAction,
          "error",
          "Failed to pull latest changes. Please check your repository connection.",
        );
        throw new Error("Failed to pull latest changes.");
      }
      emitStepEvent(basePullLatestChangesAction, "success");

      const generateCodebaseTreeActionId = uuidv4();
      const baseGenerateCodebaseTreeAction: CustomNodeEvent = {
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
          messages: createEventsMessage(),
        };
      } catch {
        emitStepEvent(
          baseGenerateCodebaseTreeAction,
          "error",
          "Failed to generate codebase tree. Please try again later.",
        );
        throw new Error("Failed to generate codebase tree.");
      }
    } catch {
      emitStepEvent(
        baseResumeSandboxAction,
        "error",
        "Failed to resume sandbox. A new environment will be created.",
      );
    }
  }

  // Creating Sandbox
  const createSandboxActionId = uuidv4();
  const baseCreateSandboxAction: CustomNodeEvent = {
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
  let sandbox: Sandbox;
  try {
    sandbox = await daytonaClient().create({ image: SNAPSHOT_NAME });
    emitStepEvent(baseCreateSandboxAction, "success");
  } catch {
    emitStepEvent(
      baseCreateSandboxAction,
      "error",
      "Failed to create sandbox environment. Please try again later.",
    );
    throw new Error("Failed to create sandbox environment.");
  }

  // Cloning repository
  const cloneRepoActionId = uuidv4();
  const baseCloneRepoAction: CustomNodeEvent = {
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
  const cloneRepoRes = await cloneRepo(sandbox, targetRepository, {
    githubInstallationToken,
    stateBranchName: state.branchName,
  });
  if (cloneRepoRes.exitCode !== 0) {
    emitStepEvent(
      baseCloneRepoAction,
      "error",
      "Failed to clone repository. Please check your repo URL and permissions.",
    );
    throw new Error("Failed to clone repository.");
  }
  emitStepEvent(baseCloneRepoAction, "success");

  // Configuring git user
  const configureGitUserActionId = uuidv4();
  const baseConfigureGitUserAction: CustomNodeEvent = {
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

  await configureGitUserInRepo(absoluteRepoDir, sandbox, {
    githubInstallationToken,
    owner: targetRepository.owner,
    repo: targetRepository.repo,
  });
  emitStepEvent(baseConfigureGitUserAction, "success");

  // Checking out branch
  const checkoutBranchActionId = uuidv4();
  const baseCheckoutBranchAction: CustomNodeEvent = {
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
  const checkoutBranchRes = await checkoutBranch(
    absoluteRepoDir,
    branchName,
    sandbox,
  );
  if (!checkoutBranchRes) {
    emitStepEvent(
      baseCheckoutBranchAction,
      "error",
      "Failed to checkout branch. Please check your branch name.",
    );
    throw new Error("Failed to checkout branch.");
  }
  emitStepEvent(baseCheckoutBranchAction, "success");

  // Generating codebase tree
  const generateCodebaseTreeActionId = uuidv4();
  const baseGenerateCodebaseTreeAction: CustomNodeEvent = {
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
      "Failed to generate codebase tree.",
    );
  }

  return {
    sandboxSessionId: sandbox.id,
    targetRepository,
    codebaseTree,
    messages: createEventsMessage(),
  };
}
