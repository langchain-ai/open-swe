import { Daytona, Sandbox, SandboxState } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../constants.js";
import { getGitHubTokensFromConfig } from "./github-tokens.js";
import { cloneRepo } from "./github/git.js";
import { FAILED_TO_GENERATE_TREE_MESSAGE, getCodebaseTree } from "./tree.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";

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

async function createSandbox(attempt: number): Promise<Sandbox | null> {
  try {
    return await daytonaClient().create(DEFAULT_SANDBOX_CREATE_PARAMS, {
      timeout: 100, // 100s timeout on creation.
    });
  } catch (e) {
    logger.error("Failed to create sandbox", {
      attempt,
      ...(e instanceof Error
        ? {
            name: e.name,
            message: e.message,
            stack: e.stack,
          }
        : {
            error: e,
          }),
    });
    return null;
  }
}

export async function getSandboxWithErrorHandling(
  sandboxSessionId: string | undefined,
  targetRepository: TargetRepository,
  branchName: string,
  config: GraphConfig,
): Promise<{
  sandbox: Sandbox;
  codebaseTree: string | null;
  dependenciesInstalled: boolean | null;
}> {
  if (isLocalMode(config)) {
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
  try {
    if (!sandboxSessionId) {
      throw new Error("No sandbox ID provided.");
    }

    logger.info("Getting sandbox.");
    // Try to get existing sandbox
    const sandbox = await daytonaClient().get(sandboxSessionId);

    // Check sandbox state
    const state = sandbox.state;

    if (state === "started") {
      return {
        sandbox,
        codebaseTree: null,
        dependenciesInstalled: null,
      };
    }

    if (state === "stopped" || state === "archived") {
      await sandbox.start();
      return {
        sandbox,
        codebaseTree: null,
        dependenciesInstalled: null,
      };
    }

    // For any other state, recreate sandbox
    throw new Error(`Sandbox in unrecoverable state: ${state}`);
  } catch (error) {
    // Recreate sandbox if any step fails
    logger.info("Recreating sandbox due to error or unrecoverable state", {
      error,
    });

    let sandbox: Sandbox | null = null;
    let numSandboxCreateAttempts = 0;
    while (!sandbox && numSandboxCreateAttempts < 3) {
      sandbox = await createSandbox(numSandboxCreateAttempts);
      if (!sandbox) {
        numSandboxCreateAttempts++;
      }
    }

    if (!sandbox) {
      throw new Error("Failed to create sandbox after 3 attempts");
    }

    const { githubInstallationToken } = getGitHubTokensFromConfig(config);

    // Clone repository
    await cloneRepo(sandbox, targetRepository, {
      githubInstallationToken,
      stateBranchName: branchName,
    });

    // Get codebase tree
    const codebaseTree = await getCodebaseTree(
      config,
      sandbox.id,
      targetRepository,
    );
    const codebaseTreeToReturn =
      codebaseTree === FAILED_TO_GENERATE_TREE_MESSAGE ? null : codebaseTree;

    logger.info("Sandbox created successfully", {
      sandboxId: sandbox.id,
    });
    return {
      sandbox,
      codebaseTree: codebaseTreeToReturn,
      dependenciesInstalled: false,
    };
  }
}
