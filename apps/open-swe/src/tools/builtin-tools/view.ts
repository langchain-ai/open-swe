import { promises as fs } from "node:fs";
import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getRepoAbsolutePath } from "@openswe/shared/git";
import { getSandboxSessionOrThrow } from "../utils/get-sandbox-id.js";
import { createViewToolFields } from "@openswe/shared/open-swe/tools";
import { handleViewCommand } from "./handlers.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@openswe/shared/open-swe/local-mode";
import { resolveLocalModePath } from "../utils/normalize-local-mode-path.js";
import { getWorkspacePathFromConfig } from "../../utils/workspace.js";

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
          const workspacePath = getWorkspacePathFromConfig(config);
          if (workspacePath) {
            result = await handleViewCommand(null, config, {
              path,
              workDir: workspacePath,
              viewRange: view_range as [number, number] | undefined,
            });
          } else {
            const { absolutePath } = resolveLocalModePath(config, path);
            const stats = await fs.stat(absolutePath);

            if (stats.isDirectory()) {
              const entries = await fs.readdir(absolutePath, { withFileTypes: true });
              const listing = entries
                .map((entry) => `${entry.isDirectory() ? "d" : "-"} ${entry.name}`)
                .join("\n");
              result = `Directory listing for ${path}:\n${listing}`;
            } else {
              const content = await fs.readFile(absolutePath, "utf-8");
              const lines = content.split("\n");
              const viewRange = view_range as [number, number] | undefined;
              if (viewRange) {
                const [start, end] = viewRange;
                const startIndex = Math.max(0, start - 1);
                const endIndex =
                  end === -1 ? lines.length : Math.min(lines.length, end);
                result = lines
                  .slice(startIndex, endIndex)
                  .map((line, index) => `${startIndex + index + 1}: ${line}`)
                  .join("\n");
              } else {
                result = lines
                  .map((line, index) => `${index + 1}: ${line}`)
                  .join("\n");
              }
            }
          }
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
    createViewToolFields(state.targetRepository, config),
  );

  return viewTool;
}
