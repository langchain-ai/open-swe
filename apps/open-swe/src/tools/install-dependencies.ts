import { tool } from "@langchain/core/tools";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createInstallDependenciesToolFields } from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";

const logger = createLogger(LogLevel.INFO, "InstallDependenciesTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

export function createInstallDependenciesTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
) {
  const installDependenciesTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const sandbox = await getSandboxSessionOrThrow(input);

        const repoRoot = getRepoAbsolutePath(state.targetRepository);
        const command = input.command.join(" ");
        const workdir = input.workdir || repoRoot;
        logger.info("Running install dependencies command", {
          command,
          workdir,
        });
        const response = await sandbox.process.executeCommand(
          command,
          workdir,
          DEFAULT_ENV,
          TIMEOUT_SEC * 2.5, // add a 2.5 min timeout
        );

        if (response.exitCode !== 0) {
          logger.error("Failed to install dependencies", {
            error: response.result,
            error_result: response,
            input,
          });
          throw new Error(
            `Command failed. Exit code: ${response.exitCode}\nResult: ${response.result}\nStdout:\n${response.artifacts?.stdout}`,
          );
        }

        return {
          result: response.result,
          status: "success",
        };
      } catch (e) {
        const errorFields = getSandboxErrorFields(e);
        if (errorFields) {
          logger.error("Failed to install dependencies", {
            input,
            error: errorFields,
          });
          throw new Error(
            `Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorFields.result ?? errorFields.artifacts?.stdout}`,
          );
        }

        logger.error(
          "Failed to install dependencies: " +
            (e instanceof Error ? e.message : "Unknown error"),
          {
            error: e,
            input,
          },
        );
        throw new Error(
          "FAILED TO INSTALL DEPENDENCIES: " +
            (e instanceof Error ? e.message : "Unknown error"),
        );
      }
    },
    createInstallDependenciesToolFields(state.targetRepository),
  );

  return installDependenciesTool;
}
