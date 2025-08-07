import { join } from "path";
import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "../utils/get-sandbox-id.js";
import { createTextEditorToolFields } from "@open-swe/shared/open-swe/tools";
import {
  handleViewCommand,
  handleStrReplaceCommand,
  handleCreateCommand,
  handleInsertCommand,
} from "./handlers.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getLocalShellExecutor } from "../../utils/shell-executor/index.js";

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
        const sandboxAbsolutePath = getRepoAbsolutePath(state.targetRepository);
        const workDir = localMode ? localAbsolutePath : sandboxAbsolutePath;
        let result: string;

        if (localMode) {
          // Local mode: use LocalShellExecutor for file operations
          const executor = getLocalShellExecutor(localAbsolutePath);

          // Convert sandbox path to local path
          let localPath = path;
          if (path.startsWith("/home/daytona/project/")) {
            // Remove the sandbox prefix to get the relative path
            localPath = path.replace("/home/daytona/project/", "");
          } else if (path.startsWith("/home/daytona/local/")) {
            // Remove the local sandbox prefix to get the relative path
            localPath = path.replace("/home/daytona/local/", "");
          }
          const filePath = join(workDir, localPath);

          switch (command) {
            case "view": {
              // Use cat command to view file content
              const viewResponse = await executor.executeCommand(
                `cat "${filePath}"`,
                {
                  workdir: workDir,
                  timeout: TIMEOUT_SEC,
                  localMode: true,
                },
              );
              if (viewResponse.exitCode !== 0) {
                throw new Error(`Failed to read file: ${viewResponse.result}`);
              }
              result = viewResponse.result;
              break;
            }
            case "str_replace": {
              if (!old_str || new_str === undefined) {
                throw new Error(
                  "str_replace command requires both old_str and new_str parameters",
                );
              }
              // Use sed command for string replacement with proper escaping
              const escapedOldStr = old_str
                .replace(/\\/g, "\\\\")
                .replace(/\//g, "\\/")
                .replace(/'/g, "'\"'\"'");
              const escapedNewStr = new_str
                .replace(/\\/g, "\\\\")
                .replace(/\//g, "\\/")
                .replace(/'/g, "'\"'\"'");

              const sedResponse = await executor.executeCommand(
                `sed -i 's/${escapedOldStr}/${escapedNewStr}/g' "${filePath}"`,
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
              // Create file with content using proper escaping
              const escapedFileText = file_text
                .replace(/\\/g, "\\\\")
                .replace(/'/g, "'\"'\"'");

              const createResponse = await executor.executeCommand(
                `echo '${escapedFileText}' > "${filePath}"`,
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
              // Insert line at specific position with proper escaping
              const escapedNewStr = new_str
                .replace(/\\/g, "\\\\")
                .replace(/\//g, "\\/")
                .replace(/'/g, "'\"'\"'");

              const insertResponse = await executor.executeCommand(
                `sed -i '${insert_line}i\\${escapedNewStr}' "${filePath}"`,
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
