import { Daytona, Sandbox, SandboxState } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../constants.js";
import { getGitHubTokensFromConfig } from "./github-tokens.js";
import { cloneRepo, configureGitUserInRepo } from "./github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getCodebaseTree } from "./tree.js";

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
  targetRepository: TargetRepository,
  branchName: string,
  config: GraphConfig,
): Promise<{
  sandbox: Sandbox;
  codebaseTree: string | null;
  dependenciesInstalled: boolean | null;
}> {
  try {
    if (!sandboxSessionId) {
      throw new Error("No sandbox ID provided.");
    }

    logger.info("Getting sandbox.");
    // Try to get existing sandbox
    const sandbox = await daytonaClient().get(sandboxSessionId);

    // Check sandbox state
    const sandboxInfo = await sandbox.info();
    const state = sandboxInfo.state;

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

    const sandbox = await daytonaClient().create(DEFAULT_SANDBOX_CREATE_PARAMS);
    const { githubInstallationToken } = getGitHubTokensFromConfig(config);

    // Clone repository
    await cloneRepo(sandbox, targetRepository, {
      githubInstallationToken,
      stateBranchName: branchName,
    });

    // Configure git user
    const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
    await configureGitUserInRepo(absoluteRepoDir, sandbox, {
      githubInstallationToken,
      owner: targetRepository.owner,
      repo: targetRepository.repo,
    });

    // Get codebase tree
    const codebaseTree = await getCodebaseTree(sandbox.id);

    logger.info("Sandbox created successfully", {
      sandboxId: sandbox.id,
    });
    return {
      sandbox,
      codebaseTree,
      dependenciesInstalled: false,
    };
  }
}
