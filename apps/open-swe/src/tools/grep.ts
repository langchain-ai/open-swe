import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { isLocalMode, getLocalWorkingDirectory } from "../utils/local-mode.js";
import { getLocalShellExecutor } from "../utils/local-shell-executor.js";
import {
  createGrepToolFields,
  formatGrepCommand,
} from "@open-swe/shared/open-swe/tools";

const logger = createLogger(LogLevel.INFO, "GrepTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

export function createGrepTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const grepTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const command = formatGrepCommand(input as any);

        let repoRoot;
        if (isLocalMode(config)) {
          // In local mode, use the local working directory
          repoRoot = getLocalWorkingDirectory();
        } else {
          // In sandbox mode, use the sandbox path
          repoRoot = getRepoAbsolutePath(state.targetRepository);
        }

        logger.info("Running grep search command", {
          command: command.join(" "),
          repoRoot,
        });

        let response;

        if (isLocalMode(config)) {
          // Local mode: use LocalShellExecutor
          const executor = getLocalShellExecutor(getLocalWorkingDirectory());

          response = await executor.executeCommand(
            command.join(" "),
            repoRoot,
            DEFAULT_ENV,
            TIMEOUT_SEC,
            true, // localMode
          );
        } else {
          // Sandbox mode: use existing sandbox logic
          const sandbox = await getSandboxSessionOrThrow(input);
          response = await sandbox.process.executeCommand(
            command.join(" "),
            repoRoot,
            DEFAULT_ENV,
            TIMEOUT_SEC,
          );
        }

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
        if (isLocalMode(config)) {
          // Local mode error handling
          throw e;
        } else {
          // Sandbox mode error handling
          const errorFields = getSandboxErrorFields(e);
          if (errorFields) {
            const errorResult =
              errorFields.result ?? errorFields.artifacts?.stdout;
            throw new Error(
              `Failed to run search command. Exit code: ${errorFields.exitCode}\nError: ${errorResult}`,
            );
          }

          throw e;
        }
      }
    },
    createGrepToolFields(state.targetRepository),
  );

  return grepTool;
}
