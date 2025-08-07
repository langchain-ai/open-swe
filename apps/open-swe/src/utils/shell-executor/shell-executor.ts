import { Sandbox } from "@daytonaio/sdk";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import { getLocalShellExecutor } from "./local-shell-executor.js";
import { createLogger, LogLevel } from "../logger.js";
import { ExecuteCommandOptions, LocalExecuteResponse } from "./types.js";
import { getSandboxSessionOrThrow } from "../../tools/utils/get-sandbox-id.js";

const logger = createLogger(LogLevel.INFO, "ShellExecutor");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

/**
 * Unified shell executor that handles both local and sandbox command execution
 * This eliminates the need for if/else blocks in every tool that runs shell commands
 */
export class ShellExecutor {
  private config?: GraphConfig;

  constructor(config?: GraphConfig) {
    this.config = config;
  }

  /**
   * Execute a command either locally or in the sandbox based on the current mode
   */
  async executeCommand(
    options: ExecuteCommandOptions,
  ): Promise<LocalExecuteResponse> {
    const {
      command,
      workdir,
      env = {},
      timeout = TIMEOUT_SEC,
      sandbox,
      sandboxSessionId,
    } = options;

    const commandString = Array.isArray(command) ? command.join(" ") : command;
    const environment = { ...DEFAULT_ENV, ...env };

    logger.info("Executing command", {
      command: commandString,
      workdir,
      localMode: isLocalMode(this.config),
    });

    if (isLocalMode(this.config)) {
      return this.executeLocal(commandString, workdir, environment, timeout);
    } else {
      return this.executeSandbox(
        commandString,
        workdir,
        environment,
        timeout,
        sandbox,
        sandboxSessionId,
      );
    }
  }

  /**
   * Execute command locally using LocalShellExecutor
   */
  private async executeLocal(
    command: string,
    workdir?: string,
    env?: Record<string, string>,
    timeout?: number,
  ): Promise<LocalExecuteResponse> {
    const executor = getLocalShellExecutor(getLocalWorkingDirectory());
    const localWorkdir = workdir || getLocalWorkingDirectory();

    return await executor.executeCommand(command, {
      workdir: localWorkdir,
      env,
      timeout,
      localMode: true,
    });
  }

  /**
   * Execute command in sandbox
   */
  private async executeSandbox(
    command: string,
    workdir?: string,
    env?: Record<string, string>,
    timeout?: number,
    sandbox?: Sandbox,
    sandboxSessionId?: string,
  ): Promise<LocalExecuteResponse> {
    const sandbox_ =
      sandbox ??
      (await getSandboxSessionOrThrow({
        xSandboxSessionId: sandboxSessionId,
      }));

    return await sandbox_.process.executeCommand(
      command,
      workdir,
      env,
      timeout,
    );
  }

  /**
   * Check if we're in local mode
   */
  checkLocalMode(): boolean {
    return isLocalMode(this.config);
  }

  /**
   * Get the appropriate working directory for the current mode
   */
  getWorkingDirectory(): string {
    if (isLocalMode(this.config)) {
      return getLocalWorkingDirectory();
    }
    // For sandbox mode, this would need to be provided by the caller
    // since it depends on the specific sandbox context
    throw new Error(
      "Working directory for sandbox mode must be provided explicitly",
    );
  }
}

/**
 * Factory function to create a ShellExecutor instance
 */
export function createShellExecutor(config?: GraphConfig): ShellExecutor {
  return new ShellExecutor(config);
}

/**
 * Convenience function for one-off command execution
 */
export async function executeCommand(
  config: GraphConfig,
  options: ExecuteCommandOptions,
): Promise<LocalExecuteResponse> {
  const executor = createShellExecutor(config);
  return await executor.executeCommand(options);
}
