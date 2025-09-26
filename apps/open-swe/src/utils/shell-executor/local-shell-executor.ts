import { spawn } from "child_process";
import { LocalExecuteResponse } from "./types.js";
import { createLogger, LogLevel } from "../logger.js";
import { TIMEOUT_SEC } from "@openswe/shared/constants";

const TIMEOUT_EXIT_CODE = 124;

const logger = createLogger(LogLevel.INFO, "LocalShellExecutor");

export class LocalShellExecutor {
  private workingDirectory: string;

  constructor(workingDirectory: string = process.cwd()) {
    this.workingDirectory = workingDirectory;
    logger.info("LocalShellExecutor created", { workingDirectory });
  }

  async executeCommand(
    command: string,
    args?: {
      workdir?: string;
      env?: Record<string, string>;
      timeout?: number;
      localMode?: boolean;
    },
  ): Promise<LocalExecuteResponse> {
    const { workdir, env, timeout = TIMEOUT_SEC, localMode = false } = args || {};
    const cwd = workdir || this.workingDirectory;
    const environment = { ...process.env, ...(env || {}) };

    logger.info("Executing command locally", { command, cwd, localMode });

    // In local mode, use spawn directly for better reliability
    if (localMode) {
      try {
        const cleanEnv = Object.fromEntries(
          Object.entries(environment).filter(([_, v]) => v !== undefined),
        ) as Record<string, string>;
        const result = await this.executeWithSpawn(
          command,
          cwd,
          cleanEnv,
          timeout,
        );
        return result;
      } catch (spawnError: any) {
        logger.error("Spawn execution failed in local mode", {
          command,
          error: spawnError.message,
        });

        return {
          exitCode: 1,
          result: spawnError.message,
          artifacts: {
            stdout: "",
            stderr: spawnError.message,
          },
        };
      }
    }

    // Non-local mode: throw error as this executor is for local mode only
    throw new Error("LocalShellExecutor is only for local mode operations");
  }

  private async executeWithSpawn(
    command: string,
    cwd: string,
    env: Record<string, string>,
    timeout: number,
  ): Promise<LocalExecuteResponse> {
    return new Promise((resolve, reject) => {
      // Try different shell paths
      const shellPaths = [
        "/bin/bash",
        "/usr/bin/bash",
        "/bin/sh",
        "/usr/bin/sh",
      ];
      let lastError: Error | null = null;

      const tryShell = (index: number) => {
        if (index >= shellPaths.length) {
          reject(lastError ?? new Error("Failed to spawn a shell to execute the command"));
          return;
        }

        const shellPath = shellPaths[index];
        const child = spawn(shellPath, ["-c", command], {
          cwd,
          env: { ...process.env, ...env },
        });

        let stdout = "";
        let stderr = "";
        let completed = false;
        let timedOut = false;
        const timeoutMs = timeout > 0 ? timeout * 1000 : undefined;
        let timeoutHandle: NodeJS.Timeout | undefined;

        const timeoutMessage = `Command timed out after ${timeout} seconds`;

        if (timeoutMs) {
          timeoutHandle = setTimeout(() => {
            timedOut = true;
            stderr = stderr.length > 0 ? `${stderr}\n${timeoutMessage}` : timeoutMessage;
            if (!child.killed) {
              try {
                child.kill("SIGKILL");
              } catch (killError) {
                logger.warn("Failed to terminate timed out local command", {
                  command,
                  error: killError instanceof Error ? killError.message : String(killError),
                });
              }
            }
          }, timeoutMs);
        }

        const finish = (result: LocalExecuteResponse) => {
          if (completed) {
            return;
          }
          completed = true;
          if (timeoutHandle) {
            clearTimeout(timeoutHandle);
          }
          resolve(result);
        };

        child.stdout?.on("data", (data) => {
          stdout += data.toString();
        });

        child.stderr?.on("data", (data) => {
          stderr += data.toString();
        });

        child.on("close", (code) => {
          if (timedOut) {
            logger.warn("Local command exceeded timeout", {
              command,
              timeoutSeconds: timeout,
            });
            finish({
              exitCode: TIMEOUT_EXIT_CODE,
              result: timeoutMessage,
              artifacts: {
                stdout,
                stderr,
              },
            });
            return;
          }

          finish({
            exitCode: code ?? 0,
            result: stdout,
            artifacts: {
              stdout,
              stderr,
            },
          });
        });

        child.on("error", (error) => {
          if (timeoutHandle) {
            clearTimeout(timeoutHandle);
          }

          if (timedOut) {
            finish({
              exitCode: TIMEOUT_EXIT_CODE,
              result: timeoutMessage,
              artifacts: {
                stdout,
                stderr,
              },
            });
            return;
          }

          lastError = error instanceof Error ? error : new Error(String(error));
          const nextIndex = index + 1;
          if (nextIndex < shellPaths.length) {
            tryShell(nextIndex);
          } else {
            reject(lastError);
          }
        });
      };

      // Start with the first shell path
      tryShell(0);
    });
  }

  getWorkingDirectory(): string {
    return this.workingDirectory;
  }

  setWorkingDirectory(directory: string): void {
    this.workingDirectory = directory;
    logger.info("Working directory changed", { workingDirectory: directory });
  }
}

let sharedExecutor: LocalShellExecutor | null = null;

export function getLocalShellExecutor(
  workingDirectory?: string,
): LocalShellExecutor {
  if (
    !sharedExecutor ||
    (workingDirectory &&
      sharedExecutor.getWorkingDirectory() !== workingDirectory)
  ) {
    sharedExecutor = new LocalShellExecutor(workingDirectory);
  }
  return sharedExecutor;
}
