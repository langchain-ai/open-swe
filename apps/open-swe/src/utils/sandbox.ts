import { Daytona, Sandbox, SandboxState } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";

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
    sandbox.instance.state == SandboxState.STOPPED ||
    sandbox.instance.state == SandboxState.ARCHIVED
  ) {
    return sandboxSessionId;
  } else if (sandbox.instance.state == "started") {
    await daytonaClient().stop(sandbox);
  }

  return sandbox.id;
}

/**
 * Starts the sandbox.
 * @param sandboxSessionId The ID of the sandbox to start.
 * @returns The sandbox client.
 */
export async function startSandbox(sandboxSessionId: string): Promise<Sandbox> {
  const sandbox = await daytonaClient().get(sandboxSessionId);
  if (
    sandbox.instance.state == SandboxState.STOPPED ||
    sandbox.instance.state == SandboxState.ARCHIVED
  ) {
    await daytonaClient().start(sandbox);
  }
  return sandbox;
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
