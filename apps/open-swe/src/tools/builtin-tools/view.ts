import { join } from "path";
import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "../utils/get-sandbox-id.js";
import { createViewToolFields } from "@open-swe/shared/open-swe/tools";
import { handleViewCommand } from "./handlers.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createShellExecutor } from "../../utils/shell-executor/index.js";

const logger = createLogger(LogLevel.INFO, "ViewTool");

export function createViewTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const viewTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const { command, path, view_range } = input as any;
        if (command !== "view") {
          throw new Error(`Unknown command: ${command}`);
        }

        const workDir = isLocalMode(config)
          ? getLocalWorkingDirectory()
          : getRepoAbsolutePath(state.targetRepository);

        let result: string;
        if (isLocalMode(config)) {
          // Local mode: use ShellExecutor for file viewing
          const executor = createShellExecutor(config);

          // Convert sandbox path to local path
          let localPath = path;
          if (path.startsWith("/home/daytona/project/")) {
            // Remove the sandbox prefix to get the relative path
            localPath = path.replace("/home/daytona/project/", "");
          }
          const filePath = join(workDir, localPath);

          // Use cat command to view file content
          const response = await executor.executeCommand({
            command: `cat "${filePath}"`,
            workdir: workDir,
            timeout: TIMEOUT_SEC,
          });

          if (response.exitCode !== 0) {
            throw new Error(`Failed to read file: ${response.result}`);
          }

          result = response.result;
        } else {
          // Sandbox mode: use existing handler
          const sandbox = await getSandboxSessionOrThrow(input);
          result = await handleViewCommand(sandbox, config, {
            path,
            workDir,
            viewRange: view_range as [number, number] | undefined,
          });
        }

        logger.info(`View command executed successfully on ${path}`);
        return { result, status: "success" };
      } catch (error) {
        const errorMessage =
          error instanceof Error ? error.message : String(error);
        logger.error(`View command failed: ${errorMessage}`);
        return {
          result: `Error: ${errorMessage}`,
          status: "error",
        };
      }
    },
    createViewToolFields(state.targetRepository),
  );

  return viewTool;
}
