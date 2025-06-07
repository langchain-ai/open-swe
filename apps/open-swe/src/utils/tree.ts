import { getCurrentTaskInput } from "@langchain/langgraph";
import { GraphState } from "../types.js";
import { daytonaClient } from "./sandbox.js";
import { createLogger, LogLevel } from "./logger.js";
import path from "node:path";
import { SANDBOX_ROOT_DIR, TIMEOUT_SEC } from "../constants.js";

const logger = createLogger(LogLevel.INFO, "Tree");

export async function getCodebaseTree(sandboxSessionId_?: string) {
  const command = `git ls-files | tree --fromfile -L 3`;
  const state = getCurrentTaskInput<GraphState>();
  const sandboxSessionId = sandboxSessionId_ || state.sandboxSessionId;
  if (!sandboxSessionId) {
    logger.error("Failed to generate tree: No sandbox session ID provided");
    throw new Error("Failed generate tree: No sandbox session ID provided");
  }
  const sandbox = await daytonaClient().get(sandboxSessionId);
  const repoDir = path.join(SANDBOX_ROOT_DIR, state.targetRepository.repo);
  const response = await sandbox.process.executeCommand(
    command,
    repoDir,
    undefined,
    TIMEOUT_SEC,
  );

  if (response.exitCode !== 0) {
    logger.error("Failed to generate tree", {
      error: response.result,
      error_result: response,
    });
    throw new Error(`Failed to generate tree: ${response.result}`);
  }

  return response.result;
}
