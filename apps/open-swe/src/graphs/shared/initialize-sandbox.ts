import { v4 as uuidv4 } from "uuid";
import * as crypto from "crypto";
import {
  CustomRules,
  GraphConfig,
  TargetRepository,
} from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getCodebaseTree } from "../../utils/tree.js";
import { createDockerSandbox } from "../../utils/sandbox.js";
import { createShellExecutor } from "../../utils/shell-executor/index.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@openswe/shared/constants";
import {
  CustomNodeEvent,
  INITIALIZE_NODE_ID,
} from "@openswe/shared/open-swe/custom-node-events";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { getCustomRules } from "../../utils/custom-rules.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@openswe/shared/open-swe/local-mode";

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

  if (isLocalMode(config)) {
    return initializeSandboxLocal(
      state,
      config,
      emitStepEvent,
      createEventsMessage,
    );
  }

  return initializeSandboxRemote(
    state,
    config,
    emitStepEvent,
    createEventsMessage,
  );
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

/**
 * Remote mode version of initializeSandbox using Docker sandbox
 */
async function initializeSandboxRemote(
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
  const repoName = `${targetRepository.owner}/${targetRepository.repo}`;

  // Step 1: Create sandbox
  const createSandboxAction: CustomNodeEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: uuidv4(),
    action: "Creating sandbox",
    data: {
      status: "pending",
      sandboxSessionId: null,
      branch: branchName,
      repo: repoName,
    },
  };
  emitStepEvent(createSandboxAction, "pending");

  let sandbox;
  try {
    const image = process.env.OPEN_SWE_SANDBOX_IMAGE || "node:18";
    const repoPath = getLocalWorkingDirectory();
    sandbox = await createDockerSandbox(image, repoPath);
    emitStepEvent(
      {
        ...createSandboxAction,
        data: { ...createSandboxAction.data, sandboxSessionId: sandbox.id },
      },
      "success",
    );
  } catch (_) {
    emitStepEvent(createSandboxAction, "error", "Failed to create sandbox.");
    throw _;
  }

  // Step 2: Copy repository into /workspace/<repo>
  const cloneRepoAction: CustomNodeEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: uuidv4(),
    action: "Cloning repository",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    },
  };
  emitStepEvent(cloneRepoAction, "pending");
  try {
    await sandbox.process.executeCommand(
      `shopt -s dotglob && mkdir -p ${targetRepository.repo} && for f in *; do [ "$f" = "${targetRepository.repo}" ] || mv "$f" ${targetRepository.repo}/; done`,
      "/workspace",
    );
    emitStepEvent(cloneRepoAction, "success");
  } catch (_) {
    emitStepEvent(cloneRepoAction, "error", "Failed to clone repository.");
  }

  const repoDir = `/workspace/${targetRepository.repo}`;

  // Step 3: Checkout branch
  const checkoutAction: CustomNodeEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: uuidv4(),
    action: "Checking out branch",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    },
  };
  emitStepEvent(checkoutAction, "pending");
  try {
    const executor = createShellExecutor(config);
    const response = await executor.executeCommand({
      command: `git checkout ${branchName}`,
      workdir: repoDir,
      sandbox,
    });
    if (response.exitCode === 0) {
      emitStepEvent(checkoutAction, "success");
    } else {
      emitStepEvent(checkoutAction, "error", "Failed to checkout branch.");
    }
  } catch (_) {
    emitStepEvent(checkoutAction, "error", "Failed to checkout branch.");
  }

  // Step 4: Generate codebase tree
  const generateTreeActionId = uuidv4();
  const baseGenerateTreeAction: CustomNodeEvent = {
    nodeId: INITIALIZE_NODE_ID,
    createdAt: new Date().toISOString(),
    actionId: generateTreeActionId,
    action: "Generating codebase tree",
    data: {
      status: "pending",
      sandboxSessionId: sandbox.id,
      branch: branchName,
      repo: repoName,
    },
  };
  emitStepEvent(baseGenerateTreeAction, "pending");

  let codebaseTree: string | undefined = undefined;
  try {
    codebaseTree = await getCodebaseTree(config, sandbox.id, targetRepository);
    emitStepEvent(baseGenerateTreeAction, "success");
  } catch (_) {
    emitStepEvent(
      baseGenerateTreeAction,
      "error",
      "Failed to generate codebase tree.",
    );
  }

  return {
    sandboxSessionId: sandbox.id,
    targetRepository,
    codebaseTree,
    messages: [...(state.messages || []), ...createEventsMessage()],
    dependenciesInstalled: false,
    customRules: await getCustomRules(sandbox, repoDir, config),
    branchName,
  };
}
