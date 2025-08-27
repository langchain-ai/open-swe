import { v4 as uuidv4 } from "uuid";
import * as crypto from "crypto";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getGitHubTokensFromConfig } from "../../utils/github-tokens.js";
import {
  CustomRules,
  GraphConfig,
  TargetRepository,
} from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { daytonaClient } from "../../utils/sandbox.js";
import { cloneRepo, pullLatestChanges } from "../../utils/github/git.js";
import {
  FAILED_TO_GENERATE_TREE_MESSAGE,
  getCodebaseTree,
} from "../../utils/tree.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import {
  CustomNodeEvent,
  INITIALIZE_NODE_ID,
} from "@open-swe/shared/open-swe/custom-node-events";
import { Sandbox } from "@daytonaio/sdk";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../../constants.js";
import { getCustomRules } from "../../utils/custom-rules.js";
import { withRetry } from "../../utils/retry.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import { DEFAULT_GITIGNORE } from "../../utils/default-gitignore.js";
import { writeFile } from "../../utils/read-write.js";

const logger = createLogger(LogLevel.INFO, "InitializeSandbox");

type InitializeSandboxState = {
  targetRepository: TargetRepository;
  branchName: string;
  sandboxSessionId?: string;
  codebaseTree?: string;
  messages?: BaseMessage[];
  dependenciesInstalled?: boolean;
  customRules?: CustomRules;
};

export async function initializeSandbox(
  state: InitializeSandboxState,
  config: GraphConfig,
): Promise<Partial<InitializeSandboxState>> {
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
        runId: config.configurable?.run_id ?? "",
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
      content: "Initialize sandbox",
      additional_kwargs: {
        hidden: true,
        customNodeEvents: events,
      },
    }),
  ];

  // Check if we're in local mode before trying to get GitHub tokens
  if (isLocalMode(config)) {
    return initializeSandboxLocal(
      state,
      config,
      emitStepEvent,
      createEventsMessage,
    );
  }

  const { githubInstallationToken } = getGitHubTokensFromConfig(config);

  if (!sandboxSessionId) {
    emitStepEvent(
      {
        nodeId: INITIALIZE_NODE_ID,
        createdAt: new Date().toISOString(),
        actionId: uuidv4(),
        action: "Resuming sandbox",
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
      action: "Resuming sandbox",
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
        {
          githubInstallationToken,
        },
      );
      if (!pullChangesRes) {
        emitStepEvent(basePullLatestChangesAction, "skipped");
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
        const codebaseTree = await getCodebaseTree(config, existingSandbox.id);
        if (codebaseTree === FAILED_TO_GENERATE_TREE_MESSAGE) {
          emitStepEvent(
            baseGenerateCodebaseTreeAction,
            "error",
            FAILED_TO_GENERATE_TREE_MESSAGE,
          );
        } else {
          emitStepEvent(baseGenerateCodebaseTreeAction, "success");
        }

        return {
          sandboxSessionId: existingSandbox.id,
          codebaseTree,
          messages: createEventsMessage(),
          customRules: await getCustomRules(
            existingSandbox,
            absoluteRepoDir,
            config,
          ),
        };
      } catch {
        emitStepEvent(
          baseGenerateCodebaseTreeAction,
          "error",
          FAILED_TO_GENERATE_TREE_MESSAGE,
        );
        return {
          sandboxSessionId: existingSandbox.id,
          codebaseTree: FAILED_TO_GENERATE_TREE_MESSAGE,
          messages: createEventsMessage(),
          customRules: await getCustomRules(
            existingSandbox,
            absoluteRepoDir,
            config,
          ),
        };
      }
    } catch {
      emitStepEvent(
        baseResumeSandboxAction,
        "skipped",
        "Unable to resume sandbox. A new environment will be created.",
      );
    }
  }

  // Creating sandbox
  const createSandboxActionId = uuidv4();
  const baseCreateSandboxAction: CustomNodeEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: createSandboxActionId,
    action: "Creating sandbox",
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
    sandbox = await daytonaClient().create(DEFAULT_SANDBOX_CREATE_PARAMS);
    emitStepEvent(baseCreateSandboxAction, "success");
  } catch (e) {
    logger.error("Failed to create sandbox environment", { e });
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

  // Retry the clone command up to 3 times. Sometimes, it can timeout if the repo is large.
  const cloneRepoRes = await withRetry(
    async () => {
      return await cloneRepo(sandbox, targetRepository, {
        githubInstallationToken,
        stateBranchName: branchName,
      });
    },
    { retries: 0, delay: 0 },
  );

  // Check if the error is due to an empty repository
  if (
    cloneRepoRes instanceof Error &&
    cloneRepoRes.message.includes("remote repository is empty")
  ) {
    return await initializeSandboxEmptyRepo({
      sandbox,
      absoluteRepoDir,
      targetRepository,
      branchName,
      config,
      emitStepEvent,
      createEventsMessage,
      baseCloneRepoAction,
    });
  }

  if (
    cloneRepoRes instanceof Error &&
    !cloneRepoRes.message.includes("repository already exists")
  ) {
    emitStepEvent(
      baseCloneRepoAction,
      "error",
      "Failed to clone repository. Please check your repo URL and permissions.",
    );
    const errorFields = {
      ...(cloneRepoRes instanceof Error
        ? {
            name: cloneRepoRes.name,
            message: cloneRepoRes.message,
            stack: cloneRepoRes.stack,
          }
        : cloneRepoRes),
    };
    logger.error("Cloning repository failed", errorFields);
    throw new Error("Failed to clone repository.");
  }
  const newBranchName =
    typeof cloneRepoRes === "string" ? cloneRepoRes : branchName;
  emitStepEvent(baseCloneRepoAction, "success");

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
      branch: newBranchName,
      repo: repoName,
    },
  };
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
      branch: newBranchName,
      repo: repoName,
    },
  };
  emitStepEvent(baseGenerateCodebaseTreeAction, "pending");
  let codebaseTree: string | undefined;
  try {
    codebaseTree = await getCodebaseTree(config, sandbox.id);
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
    dependenciesInstalled: false,
    customRules: await getCustomRules(sandbox, absoluteRepoDir, config),
    branchName: newBranchName,
  };
}

/**
 * Local mode version of initializeSandbox
 * Skips sandbox creation and repository cloning, works directly with local filesystem
 */
async function initializeSandboxLocal(
  state: InitializeSandboxState,
  config: GraphConfig,
  emitStepEvent: (
    base: CustomNodeEvent,
    status: "pending" | "success" | "error" | "skipped",
    error?: string,
  ) => void,
  createEventsMessage: () => BaseMessage[],
): Promise<Partial<InitializeSandboxState>> {
  const { targetRepository, branchName } = state;
  const absoluteRepoDir = getLocalWorkingDirectory(); // Use local working directory in local mode
  const repoName = `${targetRepository.owner}/${targetRepository.repo}`;

  // Skip sandbox creation in local mode
  emitStepEvent(
    {
      nodeId: INITIALIZE_NODE_ID,
      createdAt: new Date().toISOString(),
      actionId: uuidv4(),
      action: "Creating sandbox",
      data: {
        status: "skipped",
        sandboxSessionId: null,
        branch: branchName,
        repo: repoName,
      },
    },
    "skipped",
  );

  // Skip repository cloning in local mode
  emitStepEvent(
    {
      nodeId: INITIALIZE_NODE_ID,
      createdAt: new Date().toISOString(),
      actionId: uuidv4(),
      action: "Cloning repository",
      data: {
        status: "skipped",
        sandboxSessionId: null,
        branch: branchName,
        repo: repoName,
      },
    },
    "skipped",
  );

  // Skip branch checkout in local mode
  emitStepEvent(
    {
      nodeId: INITIALIZE_NODE_ID,
      createdAt: new Date().toISOString(),
      actionId: uuidv4(),
      action: "Checking out branch",
      data: {
        status: "skipped",
        sandboxSessionId: null,
        branch: branchName,
        repo: repoName,
      },
    },
    "skipped",
  );

  // Generate codebase tree locally
  const generateCodebaseTreeActionId = uuidv4();
  const baseGenerateCodebaseTreeAction: CustomNodeEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: generateCodebaseTreeActionId,
    action: "Generating codebase tree",
    data: {
      status: "pending",
      sandboxSessionId: null,
      branch: branchName,
      repo: repoName,
    },
  };
  emitStepEvent(baseGenerateCodebaseTreeAction, "pending");

  let codebaseTree = undefined;
  try {
    codebaseTree = await getCodebaseTree(config, undefined, targetRepository);
    emitStepEvent(baseGenerateCodebaseTreeAction, "success");
  } catch (_) {
    emitStepEvent(
      baseGenerateCodebaseTreeAction,
      "error",
      "Failed to generate codebase tree.",
    );
  }

  // Create a mock sandbox ID for consistency
  const mockSandboxId = `local-${Date.now()}-${crypto.randomBytes(16).toString("hex")}`;

  return {
    sandboxSessionId: mockSandboxId,
    targetRepository,
    codebaseTree,
    messages: [...(state.messages || []), ...createEventsMessage()],
    dependenciesInstalled: false,
    customRules: await getCustomRules(null as any, absoluteRepoDir, config),
    branchName: branchName,
  };
}

async function initializeSandboxEmptyRepo(inputs: {
  sandbox: Sandbox;
  absoluteRepoDir: string;
  targetRepository: TargetRepository;
  config: GraphConfig;
  branchName: string;
  emitStepEvent: (
    event: CustomNodeEvent,
    status: "pending" | "success" | "error" | "skipped",
    error?: string,
  ) => void;
  createEventsMessage: () => AIMessage[];
  baseCloneRepoAction: CustomNodeEvent;
}) {
  const {
    sandbox,
    absoluteRepoDir,
    targetRepository,
    config,
    emitStepEvent,
    createEventsMessage,
    branchName,
    baseCloneRepoAction,
  } = inputs;
  const repoName = `${targetRepository.owner}/${targetRepository.repo}`;

  logger.info("Detected empty repository. Initializing with default files.");

  try {
    // Step 1: Create the repository directory
    const mkdirResult = await sandbox.process.executeCommand(
      `mkdir -p ${absoluteRepoDir}`,
    );
    logger.info("Created repository directory.");

    if (mkdirResult.exitCode !== 0) {
      throw new Error(`Failed to create directory: ${mkdirResult.result}`);
    }

    // Step 2: Initialize git repository
    const gitInitResult = await sandbox.process.executeCommand(
      "git init",
      absoluteRepoDir,
    );
    logger.info("Initialized git repository.");

    if (gitInitResult.exitCode !== 0) {
      throw new Error(
        `Failed to initialize git repository: ${gitInitResult.result}`,
      );
    }

    // Step 3: Add remote origin
    const remoteUrl = `https://github.com/${targetRepository.owner}/${targetRepository.repo}.git`;
    const addRemoteResult = await sandbox.process.executeCommand(
      `git remote add origin ${remoteUrl}`,
      absoluteRepoDir,
    );
    logger.info("Added remote origin.");

    if (addRemoteResult.exitCode !== 0) {
      throw new Error(`Failed to add remote origin: ${addRemoteResult.result}`);
    }

    // Step 4: Checkout branch
    await sandbox.git.createBranch(absoluteRepoDir, branchName);
    logger.info("Created branch.");
    await sandbox.git.checkoutBranch(absoluteRepoDir, branchName);
    logger.info("Checked out branch.");

    // Step 5: Create .gitignore file
    const gitignoreFileName = ".gitignore";
    const writeResult = await writeFile({
      sandbox,
      filePath: gitignoreFileName,
      content: DEFAULT_GITIGNORE,
      workDir: absoluteRepoDir,
    });

    if (!writeResult.success) {
      throw new Error(
        `Failed to create .gitignore file: ${writeResult.output}`,
      );
    }
    logger.info("Created .gitignore file.");

    // Step 6: Stage the .gitignore file
    await sandbox.git.add(absoluteRepoDir, [gitignoreFileName]);
    logger.info("Staged .gitignore file.");

    // Step 7: Commit changes
    const botAppName = process.env.GITHUB_APP_NAME;
    if (!botAppName) {
      throw new Error("GITHUB_APP_NAME environment variable is not set.");
    }
    const userName = `${botAppName}[bot]`;
    const userEmail = `${botAppName}@users.noreply.github.com`;

    await sandbox.git.commit(
      absoluteRepoDir,
      "Initial commit with .gitignore",
      userName,
      userEmail,
    );
    logger.info("Committed changes.");

    const { githubInstallationToken } = getGitHubTokensFromConfig(config);

    // Step 8: Push to remote
    const pushResult = await withRetry(
      async () => {
        return await sandbox.git.push(
          absoluteRepoDir,
          "git",
          githubInstallationToken,
        );
      },
      { retries: 3, delay: 1000 },
    );

    if (pushResult instanceof Error) {
      throw new Error(`Failed to push initial commit: ${pushResult.message}`);
    }
    logger.info("Pushed initial commit.");

    // Step 9: Set branch name and emit success
    const newBranchName = "main";
    emitStepEvent(baseCloneRepoAction, "success");
    logger.info(
      "Successfully initialized empty repository with default files.",
    );

    // Continue with the normal flow - skip to after the branch name assignment
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
        branch: newBranchName,
        repo: repoName,
      },
    };
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
        branch: newBranchName,
        repo: repoName,
      },
    };
    emitStepEvent(baseGenerateCodebaseTreeAction, "pending");
    let codebaseTree: string | undefined;
    try {
      codebaseTree = await getCodebaseTree(sandbox.id, undefined, config);
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
      dependenciesInstalled: false,
      customRules: await getCustomRules(sandbox, absoluteRepoDir, config),
      branchName: newBranchName,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    emitStepEvent(
      baseCloneRepoAction,
      "error",
      `Failed to initialize empty repository: ${errorMessage}`,
    );
    logger.error("Failed to initialize empty repository", {
      error: errorMessage,
    });
    throw new Error(`Failed to initialize empty repository: ${errorMessage}`);
  }
}
