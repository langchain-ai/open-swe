import { v4 as uuidv4 } from "uuid";
import * as crypto from "crypto";
import {
  CustomRules,
  GraphConfig,
  TargetRepository,
} from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import {
  FAILED_TO_GENERATE_TREE_MESSAGE,
  getCodebaseTree,
} from "../../utils/tree.js";
import { DO_NOT_RENDER_ID_PREFIX } from "@open-swe/shared/constants";
import {
  CustomNodeEvent,
  INITIALIZE_NODE_ID,
} from "@open-swe/shared/open-swe/custom-node-events";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { getCustomRules } from "../../utils/custom-rules.js";

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
  const { targetRepository, branchName } = state;
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

  const absoluteRepoDir = process.env.LOCAL_REPO_PATH as string;

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
    codebaseTree = await getCodebaseTree(undefined, targetRepository, config);
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
