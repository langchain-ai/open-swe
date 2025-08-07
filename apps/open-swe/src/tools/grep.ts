import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import {
  createGrepToolFields,
  formatGrepCommand,
} from "@open-swe/shared/open-swe/tools";
import { createShellExecutor } from "../utils/shell-executor/index.js";
import { wrapScript } from "../utils/wrap-script.js";

const logger = createLogger(LogLevel.INFO, "GrepTool");

export function createGrepTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const grepTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const command = formatGrepCommand(input);
        const localMode = isLocalMode(config);
        const localAbsolutePath = getLocalWorkingDirectory();
        const sandboxAbsolutePath = getRepoAbsolutePath(state.targetRepository);
        const workDir = localMode ? localAbsolutePath : sandboxAbsolutePath;

        logger.info("Running grep search command", {
          command: command.join(" "),
          workDir,
        });

        const executor = createShellExecutor(config);
        const response = await executor.executeCommand({
          command: wrapScript(command.join(" ")),
          workdir: workDir,
          timeout: TIMEOUT_SEC,
        });

        let successResult = response.result;

        if (
          response.exitCode === 1 ||
          (response.exitCode === 127 && response.result.startsWith("sh: 1: "))
        ) {
          const errorResult = response.result ?? response.artifacts?.stdout;
          successResult = `Exit code 1. No results found.\n\n${errorResult}`;
        } else if (response.exitCode > 1) {
          const errorResult = response.result ?? response.artifacts?.stdout;
          throw new Error(
            `Failed to run grep search command. Exit code: ${response.exitCode}\nError: ${errorResult}`,
          );
        }

        return {
          result: successResult,
          status: "success",
        };
      } catch (e) {
        const errorFields = getSandboxErrorFields(e);
        if (errorFields) {
          const errorResult =
            errorFields.result ?? errorFields.artifacts?.stdout;
          return {
            result: `Failed to run search command. Exit code: ${errorFields.exitCode}\nError: ${errorResult}`,
            status: "error" as const,
          };
        }

        const errorMessage = e instanceof Error ? e.message : String(e);
        return {
          result: `Failed to run grep search command: ${errorMessage}`,
          status: "error" as const,
        };
      }
    },
    createGrepToolFields(state.targetRepository),
  );

  return grepTool;
}
