import { getCurrentTaskInput } from "@langchain/langgraph";
import { GraphState } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getSandbox } from "../../utils/sandbox.js";
import type { Sandbox } from "../../utils/sandbox.js";

const logger = createLogger(LogLevel.INFO, "GetSandboxSessionOrThrow");

export function getSandboxSessionOrThrow(
  input: Record<string, unknown>,
): Sandbox {
  let sandboxSessionId = "";
  if ("xSandboxSessionId" in input && input.xSandboxSessionId) {
    sandboxSessionId = input.xSandboxSessionId as string;
  } else {
    const state = getCurrentTaskInput<GraphState>();
    sandboxSessionId = state.sandboxSessionId;
  }

  if (!sandboxSessionId) {
    logger.error("FAILED TO RUN COMMAND: No sandbox session ID provided");
    throw new Error("FAILED TO RUN COMMAND: No sandbox session ID provided");
  }

  const sandbox = getSandbox(sandboxSessionId);
  if (!sandbox) {
    logger.error("FAILED TO RUN COMMAND: Sandbox not found", {
      sandboxSessionId,
    });
    throw new Error("FAILED TO RUN COMMAND: Sandbox not found");
  }
  return sandbox;
}
