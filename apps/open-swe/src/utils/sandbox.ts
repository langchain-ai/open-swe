import { Daytona, Sandbox, SandboxState } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { GraphConfig, TargetRepository } from "@openswe/shared/open-swe/types";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";

const logger = createLogger(LogLevel.INFO, "Sandbox");

// Singleton instance of Daytona
let daytonaInstance: Daytona | null = null;

/**
 * Returns a shared Daytona instance
 */
export function daytonaClient(): Daytona {
  if (!daytonaInstance) {
    daytonaInstance = new Daytona();
  }
  return daytonaInstance;
}

/**
 * Stops the sandbox. Either pass an existing sandbox client, or a sandbox session ID.
 * If no sandbox client is provided, the sandbox will be connected to.
 
 * @param sandboxSessionId The ID of the sandbox to stop.
 * @param sandbox The sandbox client to stop. If not provided, the sandbox will be connected to.
 * @returns The sandbox session ID.
 */
export async function stopSandbox(sandboxSessionId: string): Promise<string> {
  const sandbox = await daytonaClient().get(sandboxSessionId);
  if (
    sandbox.state === SandboxState.STOPPED ||
    sandbox.state === SandboxState.ARCHIVED
  ) {
    return sandboxSessionId;
  } else if (sandbox.state === "started") {
    await daytonaClient().stop(sandbox);
  }

  return sandbox.id;
}

/**
 * Deletes the sandbox.
 * @param sandboxSessionId The ID of the sandbox to delete.
 * @returns True if the sandbox was deleted, false if it failed to delete.
 */
export async function deleteSandbox(
  sandboxSessionId: string,
): Promise<boolean> {
  try {
    const sandbox = await daytonaClient().get(sandboxSessionId);
    await daytonaClient().delete(sandbox);
    return true;
  } catch (error) {
    logger.error("Failed to delete sandbox", {
      sandboxSessionId,
      error,
    });
    return false;
  }
}

export async function getSandboxWithErrorHandling(
  sandboxSessionId: string | undefined,
  _targetRepository: TargetRepository,
  _branchName: string,
  config: GraphConfig,
): Promise<{
  sandbox: Sandbox;
  codebaseTree: string | null;
  dependenciesInstalled: boolean | null;
}> {
  if (!isLocalMode(config)) {
    throw new Error("Sandbox operations are only supported in local mode");
  }

  const mockSandbox = {
    id: sandboxSessionId || "local-mock-sandbox",
    state: "started",
  } as Sandbox;

  return {
    sandbox: mockSandbox,
    codebaseTree: null,
    dependenciesInstalled: null,
  };
}
