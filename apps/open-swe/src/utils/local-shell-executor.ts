import { exec, spawn } from "child_process";
import { promisify } from "util";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "LocalShellExecutor");
const execAsync = promisify(exec);

export interface ExecuteResponse {
  exitCode: number;
  result: string;
  artifacts?: {
    stdout: string;
    stderr: string;
  };
}

export class LocalShellExecutor {
  private workingDirectory: string;

  constructor(workingDirectory: string = process.cwd()) {
    this.workingDirectory = workingDirectory;
    logger.info("LocalShellExecutor created", { workingDirectory });
  }

  async executeCommand(
    command: string,
    workdir?: string,
    env?: Record<string, string>,
    timeout: number = 30,
    localMode: boolean = false,
  ): Promise<ExecuteResponse> {
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

    // Non-local mode: try exec first, fallback to spawn
    try {
      const { stdout, stderr } = await execAsync(command, {
        cwd,
        env: environment,
        timeout: timeout * 1000, // Convert to milliseconds
        maxBuffer: 1024 * 1024 * 10, // 10MB buffer
        shell: process.env.SHELL || "/bin/bash",
      });

      return {
        exitCode: 0,
        result: stdout,
        artifacts: {
          stdout,
          stderr,
        },
      };
    } catch (error: any) {
      logger.error("Command execution failed with exec, trying spawn", {
        command,
        error: error.message,
      });

      // Fallback to spawn if exec fails
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

        logger.error("Spawn fallback succeeded", {
          command,
          result,
        });
        return result;
      } catch (spawnError: any) {
        logger.error("Spawn fallback also failed", {
          command,
          error: spawnError.message,
        });

        return {
          exitCode: error.code || 1,
          result: error.stdout || error.message,
          artifacts: {
            stdout: error.stdout || "",
            stderr: error.stderr || error.message,
          },
        };
      }
    }
  }

  private async executeWithSpawn(
    command: string,
    cwd: string,
    env: Record<string, string>,
    timeout: number,
  ): Promise<ExecuteResponse> {
    return new Promise((resolve, reject) => {
      // Try different shell paths
      const shellPaths = [
        "/bin/bash",
        "/usr/bin/bash",
        "/bin/sh",
        "/usr/bin/sh",
      ];
      let lastError: Error | null = null;

      const tryShell = (shellPath: string) => {
        const child = spawn(shellPath, ["-c", command], {
          cwd,
          env: { ...process.env, ...env },
          timeout: timeout * 1000,
        });

        let stdout = "";
        let stderr = "";

        child.stdout?.on("data", (data) => {
          stdout += data.toString();
        });

        child.stderr?.on("data", (data) => {
          stderr += data.toString();
        });

        child.on("close", (code) => {
          resolve({
            exitCode: code || 0,
            result: stdout,
            artifacts: {
              stdout,
              stderr,
            },
          });
        });

        child.on("error", (error) => {
          lastError = error;
          // Try next shell path
          const nextIndex = shellPaths.indexOf(shellPath) + 1;
          if (nextIndex < shellPaths.length) {
            tryShell(shellPaths[nextIndex]);
          } else {
            reject(lastError);
          }
        });
      };

      // Start with the first shell path
      tryShell(shellPaths[0]);
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
