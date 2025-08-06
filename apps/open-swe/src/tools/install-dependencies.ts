import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createInstallDependenciesToolFields } from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { createShellExecutor } from "../utils/shell-executor/index.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";

const logger = createLogger(LogLevel.INFO, "InstallDependenciesTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

export function createInstallDependenciesTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const installDependenciesTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const repoRoot = getRepoAbsolutePath(state.targetRepository);
        const command = input.command.join(" ");
        const workdir = input.workdir || repoRoot;
        logger.info("Running install dependencies command", {
          command,
          workdir,
        });

        // Use unified shell executor
        const executor = createShellExecutor(config);
        const sandbox = isLocalMode(config)
          ? undefined
          : await getSandboxSessionOrThrow(input);
        const response = await executor.executeCommand({
          command,
          workdir: workdir,
          env: DEFAULT_ENV,
          timeout: TIMEOUT_SEC * 2.5, // add a 2.5 min timeout
          sandbox,
        });

        if (response.exitCode !== 0) {
          const errorResult = response.result ?? response.artifacts?.stdout;
          throw new Error(
            `Failed to install dependencies. Exit code: ${response.exitCode}\nError: ${errorResult}`,
          );
        }

        return {
          result: response.result,
          status: "success",
        };
      } catch (e) {
        // Unified error handling
        const errorFields = getSandboxErrorFields(e);
        if (errorFields) {
          const errorResult =
            errorFields.result ?? errorFields.artifacts?.stdout;
          throw new Error(
            `Failed to install dependencies. Exit code: ${errorFields.exitCode}\nError: ${errorResult}`,
          );
        }

        throw e;
      }
    },
    createInstallDependenciesToolFields(state.targetRepository),
  );

  return installDependenciesTool;
}
