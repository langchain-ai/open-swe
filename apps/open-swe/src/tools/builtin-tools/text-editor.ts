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
} from "../../utils/local-mode.js";
import { getLocalShellExecutor } from "../../utils/local-shell-executor.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";

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

        let workDir;
        if (isLocalMode(config)) {
          // In local mode, use the local working directory
          workDir = getLocalWorkingDirectory();
        } else {
          // In sandbox mode, use the sandbox path
          workDir = getRepoAbsolutePath(state.targetRepository);
        }

        let result: string;

        if (isLocalMode(config)) {
          // Local mode: use LocalShellExecutor for file operations
          const executor = getLocalShellExecutor(getLocalWorkingDirectory());

          // Convert sandbox path to local path
          let localPath = path;
          if (path.startsWith("/home/daytona/project/")) {
            // Remove the sandbox prefix to get the relative path
            localPath = path.replace("/home/daytona/project/", "");
          }
          const filePath = `${workDir}/${localPath}`;

          switch (command) {
            case "view": {
              // Use cat command to view file content
              const viewResponse = await executor.executeCommand(
                `cat "${filePath}"`,
                workDir,
                {},
                TIMEOUT_SEC,
                true, // localMode
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
                workDir,
                {},
                TIMEOUT_SEC,
                true, // localMode
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
              // Create file with content using a more robust approach
              const createResponse = await executor.executeCommand(
                `cat > "${filePath}" << 'EOF'\n${file_text}\nEOF`,
                workDir,
                {},
                TIMEOUT_SEC,
                true, // localMode
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
                workDir,
                {},
                TIMEOUT_SEC,
                true, // localMode
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
              result = await handleViewCommand(
                sandbox,
                path,
                workDir,
                view_range,
              );
              break;
            case "str_replace":
              if (!old_str || new_str === undefined) {
                throw new Error(
                  "str_replace command requires both old_str and new_str parameters",
                );
              }
              result = await handleStrReplaceCommand(
                sandbox,
                path,
                workDir,
                old_str,
                new_str,
              );
              break;
            case "create":
              if (!file_text) {
                throw new Error("create command requires file_text parameter");
              }
              result = await handleCreateCommand(
                sandbox,
                path,
                workDir,
                file_text,
              );
              break;
            case "insert":
              if (insert_line === undefined || new_str === undefined) {
                throw new Error(
                  "insert command requires both insert_line and new_str parameters",
                );
              }
              result = await handleInsertCommand(
                sandbox,
                path,
                workDir,
                insert_line,
                new_str,
              );
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
