#!/usr/bin/env node

import { spawn, ChildProcess, exec } from "child_process";
import { Command } from "commander";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { promisify } from "util";
import { promptForMissingConfig, applyConfigToEnv } from "@open-swe/shared";

const execAsync = promisify(exec);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const program = new Command();

interface ServerProcess {
  process: ChildProcess;
  name: string;
  ready: boolean;
}

class OpenSWEOrchestrator {
  private langGraphServer: ServerProcess | null = null;
  private cliProcess: ServerProcess | null = null;
  private isShuttingDown = false;
  private workspaceRoot: string;

  constructor() {
    // Find the workspace root (where the main package.json is)
    this.workspaceRoot = this.findWorkspaceRoot();

    // Handle graceful shutdown
    process.on("SIGINT", () => this.shutdown());
    process.on("SIGTERM", () => this.shutdown());
    process.on("exit", () => this.shutdown());
  }

  private async ensureLangGraphCLI(): Promise<void> {
    try {
      // Check if langgraphjs command exists
      await execAsync("which langgraphjs");
    } catch {
      console.log("Installing @langchain/langgraph-cli globally...");
      try {
        await execAsync("npm install -g @langchain/langgraph-cli");
        console.log("✓ LangGraph CLI installed successfully");
      } catch (error) {
        throw new Error(`Failed to install LangGraph CLI: ${error}`);
      }
    }
  }

  private findWorkspaceRoot(): string {
    let currentDir = process.cwd();

    // Walk up the directory tree to find the workspace root
    while (currentDir !== path.parse(currentDir).root) {
      const packageJsonPath = path.join(currentDir, "package.json");
      if (fs.existsSync(packageJsonPath)) {
        try {
          const packageJson = JSON.parse(
            fs.readFileSync(packageJsonPath, "utf8"),
          );
          if (packageJson.workspaces) {
            return currentDir;
          }
        } catch {
          // Ignore JSON parsing errors and continue searching
        }
      }
      currentDir = path.dirname(currentDir);
    }
    return path.resolve(__dirname, "../../..");
  }

  private async startLangGraphServer(
    useTerminal: boolean = false,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const langGraphPath = path.join(
        this.workspaceRoot,
        "apps",
        "open-swe-v2",
      );

      if (!fs.existsSync(langGraphPath)) {
        reject(new Error(`LangGraph server path not found: ${langGraphPath}`));
        return;
      }

      let serverProcess: ChildProcess;

      if (useTerminal) {
        // Open in separate terminal window
        const terminalCommand =
          process.platform === "darwin"
            ? [
                "osascript",
                "-e",
                `tell application "Terminal" to do script "cd '${langGraphPath}' && langgraphjs dev --no-browser"`,
              ]
            : process.platform === "win32"
              ? [
                  "cmd",
                  "/c",
                  "start",
                  "cmd",
                  "/k",
                  `cd /d "${langGraphPath}" && langgraphjs dev --no-browser`,
                ]
              : [
                  "gnome-terminal",
                  "--",
                  "bash",
                  "-c",
                  `cd '${langGraphPath}' && langgraphjs dev --no-browser; read`,
                ];

        serverProcess = spawn(terminalCommand[0], terminalCommand.slice(1), {
          env: process.env,
          detached: true,
          stdio: "ignore",
        });

        // For terminal mode, we can't detect when it's ready, so just wait a bit
        setTimeout(() => {
          if (this.langGraphServer) {
            this.langGraphServer.ready = true;
            resolve();
          }
        }, 5000);
      } else {
        // Background mode (existing behavior)
        serverProcess = spawn("langgraphjs", ["dev", "--no-browser"], {
          cwd: langGraphPath,
          stdio: ["pipe", "pipe", "pipe"],
          env: process.env,
        });
      }

      this.langGraphServer = {
        process: serverProcess,
        name: "LangGraph Server",
        ready: false,
      };

      if (!useTerminal) {
        serverProcess.stdout?.on("data", (data) => {
          const output = data.toString();

          // Check if server is ready (but don't log all output)
          if (
            output.includes("Server running at") ||
            output.includes("localhost:2024") ||
            output.includes("::1:2024") ||
            output.includes("Starting 10 workers")
          ) {
            this.langGraphServer!.ready = true;
            resolve();
          }
        });

        serverProcess.stderr?.on("data", (data) => {
          const output = data.toString();
          if (
            output.toLowerCase().includes("error") &&
            !output.includes("Warning:")
          ) {
            console.error(`[LangGraph Error] ${output.trim()}`);
          }
        });
      }

      serverProcess.on("error", (error) => {
        reject(error);
      });

      serverProcess.on("exit", () => {
        if (!this.isShuttingDown) {
          this.shutdown();
        }
      });

      // Timeout if server doesn't start within 30 seconds (only for background mode)
      if (!useTerminal) {
        setTimeout(() => {
          if (!this.langGraphServer?.ready) {
            reject(
              new Error("LangGraph server failed to start within 30 seconds"),
            );
          }
        }, 30000);
      }
    });
  }

  private async startCLI(args: string[] = []): Promise<void> {
    return new Promise((resolve, reject) => {
      const cliPath = path.join(this.workspaceRoot, "apps", "cli");

      if (!fs.existsSync(cliPath)) {
        reject(new Error(`CLI path not found: ${cliPath}`));
        return;
      }

      // Build the CLI command - run the built version
      const cliProcess = spawn("node", ["dist/index.js", ...args], {
        cwd: cliPath,
        stdio: "inherit",
        env: {
          ...process.env,
          LANGGRAPH_URL: "http://localhost:2024",
          OPEN_SWE_LOCAL_PROJECT_PATH: process.cwd(),
        },
      });

      this.cliProcess = {
        process: cliProcess,
        name: "OpenSWE CLI",
        ready: true,
      };

      cliProcess.on("error", (error) => {
        reject(error);
      });

      cliProcess.on("exit", (code, _signal) => {
        if (!this.isShuttingDown) {
          // CLI exited unexpectedly
        }
        this.shutdown();
        process.exit(code || 0);
      });

      resolve();
    });
  }

  private async shutdown(): Promise<void> {
    if (this.isShuttingDown) return;
    this.isShuttingDown = true;

    const shutdownPromises: Promise<void>[] = [];

    if (this.cliProcess?.process) {
      shutdownPromises.push(
        new Promise<void>((resolve) => {
          this.cliProcess!.process.on("exit", () => resolve());
          this.cliProcess!.process.kill("SIGTERM");
          setTimeout(() => {
            if (!this.cliProcess!.process.killed) {
              this.cliProcess!.process.kill("SIGKILL");
            }
            resolve();
          }, 5000);
        }),
      );
    }

    if (this.langGraphServer?.process) {
      shutdownPromises.push(
        new Promise<void>((resolve) => {
          this.langGraphServer!.process.on("exit", () => resolve());
          this.langGraphServer!.process.kill("SIGTERM");
          setTimeout(() => {
            if (!this.langGraphServer!.process.killed) {
              this.langGraphServer!.process.kill("SIGKILL");
            }
            resolve();
          }, 5000);
        }),
      );
    }

    await Promise.all(shutdownPromises);
  }

  public async start(
    cliArgs: string[] = [],
    useTerminal: boolean = false,
  ): Promise<void> {
    try {
      // Ensure LangGraph CLI is installed globally
      await this.ensureLangGraphCLI();

      // Check and prompt for missing configuration first
      await promptForMissingConfig();

      // Apply configuration to environment
      applyConfigToEnv();

      // Start LangGraph server first
      await this.startLangGraphServer(useTerminal);

      // Wait a moment for server to fully initialize
      await new Promise((resolve) => setTimeout(resolve, 2000));

      // Start CLI
      await this.startCLI(cliArgs);
    } catch {
      await this.shutdown();
      process.exit(1);
    }
  }
}

// CLI Definition
program
  .name("openswe")
  .description(
    "OpenSWE - Unified CLI tool for running OpenSWE CLI + LangGraph server",
  )
  .version("1.0.0")
  .option("--server", "Run LangGraph server in a separate terminal window")
  .helpOption("-h, --help", "Display help for command")
  .action(async (options) => {
    const orchestrator = new OpenSWEOrchestrator();
    await orchestrator.start([], options.server);
  });

// Parse command line arguments
program.parse();
