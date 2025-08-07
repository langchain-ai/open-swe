import { getCurrentTaskInput } from "@langchain/langgraph";
import {
  GraphState,
  TargetRepository,
  GraphConfig,
} from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";
import path from "node:path";
import { SANDBOX_ROOT_DIR, TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getSandboxErrorFields } from "./sandbox-error-fields.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { createShellExecutor } from "./shell-executor/index.js";

const logger = createLogger(LogLevel.INFO, "Tree");

export const FAILED_TO_GENERATE_TREE_MESSAGE =
  "Failed to generate tree. Please try again.";

export async function getCodebaseTree(
  config: GraphConfig,
  sandboxSessionId_?: string,
  targetRepository_?: TargetRepository,
): Promise<string> {
  try {
    const command = `git ls-files | tree --fromfile -L 3`;
    let sandboxSessionId = sandboxSessionId_;
    let targetRepository = targetRepository_;

    // Check if we're in local mode
    if (isLocalMode(config)) {
      return getCodebaseTreeLocal(config);
    }

    // If sandbox session ID is not provided, try to get it from the current state.
    if (!sandboxSessionId || !targetRepository) {
      try {
        const state = getCurrentTaskInput<GraphState>();
        // Prefer the provided sandbox session ID and target repository. Fallback to state if defined.
        sandboxSessionId = sandboxSessionId ?? state.sandboxSessionId;
        targetRepository = targetRepository ?? state.targetRepository;
      } catch {
        // not executed in a LangGraph instance. continue.
      }
    }

    if (!sandboxSessionId) {
      logger.error("Failed to generate tree: No sandbox session ID provided");
      throw new Error("Failed generate tree: No sandbox session ID provided");
    }
    if (!targetRepository) {
      logger.error("Failed to generate tree: No target repository provided");
      throw new Error("Failed generate tree: No target repository provided");
    }

    const executor = createShellExecutor(config);
    const repoDir = path.join(SANDBOX_ROOT_DIR, targetRepository.repo);
    const response = await executor.executeCommand({
      command,
      workdir: repoDir,
      timeout: TIMEOUT_SEC,
      sandboxSessionId,
    });

    if (response.exitCode !== 0) {
      logger.error("Failed to generate tree", {
        exitCode: response.exitCode,
        result: response.result ?? response.artifacts?.stdout,
      });
      throw new Error(
        `Failed to generate tree: ${response.result ?? response.artifacts?.stdout}`,
      );
    }

    return response.result;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Failed to generate tree", {
      ...(errorFields ? { errorFields } : {}),
      ...(e instanceof Error
        ? {
            name: e.name,
            message: e.message,
            stack: e.stack,
          }
        : {}),
    });
    return FAILED_TO_GENERATE_TREE_MESSAGE;
  }
}

/**
 * Local version of getCodebaseTree using ShellExecutor
 */
async function getCodebaseTreeLocal(config: GraphConfig): Promise<string> {
  try {
    const executor = createShellExecutor(config);
    const command = `git ls-files | tree --fromfile -L 3`;

    const response = await executor.executeCommand({
      command,
      timeout: TIMEOUT_SEC,
    });

    if (response.exitCode !== 0) {
      logger.error("Failed to generate tree in local mode", {
        exitCode: response.exitCode,
        result: response.result,
      });
      throw new Error(
        `Failed to generate tree in local mode: ${response.result}`,
      );
    }

    return response.result;
  } catch (e) {
    logger.error("Failed to generate tree in local mode", {
      ...(e instanceof Error
        ? {
            name: e.name,
            message: e.message,
            stack: e.stack,
          }
        : { error: e }),
    });
    return FAILED_TO_GENERATE_TREE_MESSAGE;
  }
}
