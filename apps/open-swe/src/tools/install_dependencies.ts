import { tool } from "@langchain/core/tools";
import { Sandbox } from "@daytonaio/sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { daytonaClient } from "../utils/sandbox.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createInstallDependenciesToolFields } from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";

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
      let sandbox: Sandbox | undefined;
      try {
        const state = getCurrentTaskInput<GraphState>();
        const { sandboxSessionId } = state;
        if (!sandboxSessionId) {
          logger.error(
            "FAILED TO INSTALL DEPENDENCIES: No sandbox session ID provided",
            {
              input,
            },
          );
          throw new Error(
            "FAILED TO INSTALL DEPENDENCIES: No sandbox session ID provided",
          );
        }

        const repoRoot = getRepoAbsolutePath(state.targetRepository);

        sandbox = await daytonaClient().get(sandboxSessionId);
        const command = input.command;
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
