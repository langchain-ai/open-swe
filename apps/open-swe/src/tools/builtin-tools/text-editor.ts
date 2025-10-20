import { promises as fs } from "node:fs";
import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getRepoAbsolutePath } from "@openswe/shared/git";
import { getSandboxSessionOrThrow } from "../utils/get-sandbox-id.js";
import { createTextEditorToolFields } from "@openswe/shared/open-swe/tools";
import {
  handleViewCommand,
  handleStrReplaceCommand,
  handleCreateCommand,
  handleInsertCommand,
} from "./handlers.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@openswe/shared/open-swe/local-mode";
import { TIMEOUT_SEC } from "@openswe/shared/constants";
import { getLocalShellExecutor } from "../../utils/shell-executor/index.js";
import { getWorkspacePathFromConfig } from "../../utils/workspace.js";
import { resolveLocalModePath } from "../utils/normalize-local-mode-path.js";

const logger = createLogger(LogLevel.INFO, "TextEditorTool");

export function createTextEditorTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const textEditorTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const {
          command,
          path,
          view_range,
          old_str,
          new_str,
          file_text,
          insert_line,
        } = input;

        const localMode = isLocalMode(config);
        const localAbsolutePath = getLocalWorkingDirectory();
        const workspacePath = getWorkspacePathFromConfig(config);
        const sandboxAbsolutePath = getRepoAbsolutePath(
          state.targetRepository,
          config,
        );
        const workDir = workspacePath
          ? workspacePath
          : localMode
            ? localAbsolutePath
            : sandboxAbsolutePath;
        const useWorkspaceFs = Boolean(workspacePath);
        let result: string;

        if (useWorkspaceFs) {
          const sandbox = null;
          switch (command) {
            case "view":
              result = await handleViewCommand(sandbox, config, {
                path,
                workDir,
                viewRange: view_range,
              });
              break;
            case "str_replace":
              if (!old_str || new_str === undefined) {
                throw new Error(
                  "str_replace command requires both old_str and new_str parameters",
                );
              }
              result = await handleStrReplaceCommand(sandbox, config, {
                path,
                workDir,
                oldStr: old_str,
                newStr: new_str,
              });
              break;
            case "create":
              if (!file_text) {
                throw new Error("create command requires file_text parameter");
              }
              result = await handleCreateCommand(sandbox, config, {
                path,
                workDir,
                fileText: file_text,
              });
              break;
            case "insert":
              if (insert_line === undefined || new_str === undefined) {
                throw new Error(
                  "insert command requires both insert_line and new_str parameters",
                );
              }
              result = await handleInsertCommand(sandbox, config, {
                path,
                workDir,
                insertLine: insert_line,
                newStr: new_str,
              });
              break;
            default:
              throw new Error(`Unknown command: ${command}`);
          }
        } else if (localMode) {
          const { absolutePath } = resolveLocalModePath(config, path);
          const executor = getLocalShellExecutor(localAbsolutePath);

          switch (command) {
            case "view": {
              const stats = await fs.stat(absolutePath);
              if (stats.isDirectory()) {
                const entries = await fs.readdir(absolutePath, {
                  withFileTypes: true,
                });
                const listing = entries
                  .map((entry) => `${entry.isDirectory() ? "d" : "-"} ${entry.name}`)
                  .join("\n");
                result = `Directory listing for ${path}:\n${listing}`;
                break;
              }

              const content = await fs.readFile(absolutePath, "utf-8");
              const lines = content.split("\n");
              if (view_range) {
                const [start, end] = view_range;
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
              break;
            }
            case "str_replace": {
              if (!old_str || new_str === undefined) {
                throw new Error(
                  "str_replace command requires both old_str and new_str parameters",
                );
              }
              const escapedOldStr = old_str
                .replace(/\\/g, "\\\\")
                .replace(/\//g, "\\/")
                .replace(/'/g, "'\"'\"'");
              const escapedNewStr = new_str
                .replace(/\\/g, "\\\\")
                .replace(/\//g, "\\/")
                .replace(/'/g, "'\"'\"'");

              const sedResponse = await executor.executeCommand(
                `sed -i 's/${escapedOldStr}/${escapedNewStr}/g' "${absolutePath}"`,
                {
                  workdir: workDir,
                  timeout: TIMEOUT_SEC,
                  localMode: true,
                },
              );
              if (sedResponse.exitCode !== 0) {
                throw new Error(
                  `Failed to replace string: ${sedResponse.result}`,
                );
              }
              result = `Successfully replaced '${old_str}' with '${new_str}' in ${path}`;
              break;
            }
            case "create": {
              if (!file_text) {
                throw new Error("create command requires file_text parameter");
              }
              const escapedFileText = file_text
                .replace(/\\/g, "\\\\")
                .replace(/'/g, "'\"'\"'");

              const createResponse = await executor.executeCommand(
                `echo '${escapedFileText}' > "${absolutePath}"`,
                {
                  workdir: workDir,
                  timeout: TIMEOUT_SEC,
                  localMode: true,
                },
              );
              if (createResponse.exitCode !== 0) {
                throw new Error(
                  `Failed to create file: ${createResponse.result}`,
                );
              }
              result = `Successfully created file ${path}`;
              break;
            }
            case "insert": {
              if (insert_line === undefined || new_str === undefined) {
                throw new Error(
                  "insert command requires both insert_line and new_str parameters",
                );
              }
              const escapedNewStr = new_str
                .replace(/\\/g, "\\\\")
                .replace(/\//g, "\\/")
                .replace(/'/g, "'\"'\"'");

              const insertResponse = await executor.executeCommand(
                `sed -i '${insert_line}i\\${escapedNewStr}' "${absolutePath}"`,
                {
                  workdir: workDir,
                  timeout: TIMEOUT_SEC,
                  localMode: true,
                },
              );
              if (insertResponse.exitCode !== 0) {
                throw new Error(
                  `Failed to insert line: ${insertResponse.result}`,
                );
              }
              result = `Successfully inserted line at position ${insert_line} in ${path}`;
              break;
            }
            default:
              throw new Error(`Unknown command: ${command}`);
          }
        } else {
          // Sandbox mode: use existing handler
          const sandbox = await getSandboxSessionOrThrow(input);

          switch (command) {
            case "view":
              result = await handleViewCommand(sandbox, config, {
                path,
                workDir,
                viewRange: view_range,
              });
              break;
            case "str_replace":
              if (!old_str || new_str === undefined) {
                throw new Error(
                  "str_replace command requires both old_str and new_str parameters",
                );
              }
              result = await handleStrReplaceCommand(sandbox, config, {
                path,
                workDir,
                oldStr: old_str,
                newStr: new_str,
              });
              break;
            case "create":
              if (!file_text) {
                throw new Error("create command requires file_text parameter");
              }
              result = await handleCreateCommand(sandbox, config, {
                path,
                workDir,
                fileText: file_text,
              });
              break;
            case "insert":
              if (insert_line === undefined || new_str === undefined) {
                throw new Error(
                  "insert command requires both insert_line and new_str parameters",
                );
              }
              result = await handleInsertCommand(sandbox, config, {
                path,
                workDir,
                insertLine: insert_line,
                newStr: new_str,
              });
              break;
            default:
              throw new Error(`Unknown command: ${command}`);
          }
        }

        logger.info(
          `Text editor command '${command}' executed successfully on ${path}`,
        );
        return { result, status: "success" };
      } catch (error) {
        const errorMessage =
          error instanceof Error ? error.message : String(error);
        logger.error(`Text editor command failed: ${errorMessage}`);
        return {
          result: `Error: ${errorMessage}`,
          status: "error",
        };
      }
    },
    createTextEditorToolFields(state.targetRepository, config),
  );

  return textEditorTool;
}
