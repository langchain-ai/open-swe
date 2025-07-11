import { tool } from "@langchain/core/tools";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import {
  createRgToolFields,
  formatRgCommand,
} from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { wrapScript } from "../utils/wrap-script.js";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";

const logger = createLogger(LogLevel.INFO, "RgTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

export function createRgTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
) {
  const rgTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const sandbox = await getSandboxSessionOrThrow(input);

        const repoRoot = getRepoAbsolutePath(state.targetRepository);
        const command = formatRgCommand({
          pattern: input.pattern,
          paths: input.paths,
          flags: input.flags,
        });
        logger.info("Running rg command", {
          command: command.join(" "),
          repoRoot,
        });
        const response = await sandbox.process.executeCommand(
          wrapScript(command.join(" ")),
          repoRoot,
          DEFAULT_ENV,
          TIMEOUT_SEC,
        );

        let successResult = response.result;

        if (
          response.exitCode === 1 ||
          (response.exitCode === 127 && response.result.startsWith("sh: 1: "))
        ) {
          logger.info("Exit code 1. no results found", {
            ...response,
          });
          successResult = `Exit code 1. No results found.\n\n${response.result}`;
        } else if (response.exitCode > 1) {
          logger.error("Failed to run rg command", {
            error: response.result,
            error_result: response,
            input,
          });
          throw new Error(
            `Command failed. Exit code: ${response.exitCode}\nResult: ${response.result}\nStdout:\n${response.artifacts?.stdout}`,
          );
        }

        return {
          result: successResult,
          status: "success",
        };
      } catch (e) {
        const errorFields = getSandboxErrorFields(e);
        if (errorFields) {
          logger.error("Failed to run rg command", {
            input,
            error: errorFields,
          });
          throw new Error(
            `Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorFields.result ?? errorFields.artifacts?.stdout}`,
          );
        }

        logger.error(
          "Failed to run rg command: " +
            (e instanceof Error ? e.message : "Unknown error"),
          {
            error: e,
            input,
          },
        );
        throw new Error(
          "FAILED TO RUN RG COMMAND: " +
            (e instanceof Error ? e.message : "Unknown error"),
        );
      }
    },
    createRgToolFields(state.targetRepository),
  );

  return rgTool;
}
