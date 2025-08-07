import { getCurrentTaskInput } from "@langchain/langgraph";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { daytonaClient } from "../../utils/sandbox.js";
import { Sandbox } from "@daytonaio/sdk";

const logger = createLogger(LogLevel.INFO, "GetSandboxSessionOrThrow");

export async function getSandboxSessionOrThrow(
  input: Record<string, unknown>,
): Promise<Sandbox> {
  let sandboxSessionId = "";
  // Attempt to extract from input.
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

  const sandbox = await daytonaClient().get(sandboxSessionId);
  return sandbox;
}
