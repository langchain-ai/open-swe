import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createShellToolFields } from "@open-swe/shared/open-swe/tools";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { isLocalMode, getLocalWorkingDirectory } from "../utils/local-mode.js";
import { getLocalShellExecutor } from "../utils/local-shell-executor.js";

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

export function createShellTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const shellTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const { command, workdir, timeout } = input;

        if (isLocalMode(config)) {
          // Local mode: use LocalShellExecutor with local working directory
          const executor = getLocalShellExecutor(getLocalWorkingDirectory());
          const response = await executor.executeCommand(
            command.join(" "),
            getLocalWorkingDirectory(), // Always use local working directory in local mode
            DEFAULT_ENV,
            timeout ?? TIMEOUT_SEC,
            true, // localMode
          );

          if (response.exitCode !== 0) {
            const errorResult = response.result ?? response.artifacts?.stdout;
            throw new Error(
              `Command failed. Exit code: ${response.exitCode}\nResult: ${errorResult}`,
            );
          }

          return {
            result: response.result ?? `exit code: ${response.exitCode}`,
            status: "success",
          };
        } else {
          // Sandbox mode: use existing sandbox logic
          const sandbox = await getSandboxSessionOrThrow(input);

          const response = await sandbox.process.executeCommand(
            command.join(" "),
            workdir,
            DEFAULT_ENV,
            timeout ?? TIMEOUT_SEC,
          );

          if (response.exitCode !== 0) {
            const errorResult = response.result ?? response.artifacts?.stdout;
            throw new Error(
              `Command failed. Exit code: ${response.exitCode}\nResult: ${errorResult}`,
            );
          }

          return {
            result: response.result ?? `exit code: ${response.exitCode}`,
            status: "success",
          };
        }
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
              `Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorResult}`,
            );
          }

          throw e;
        }
      }
    },
    createShellToolFields(state.targetRepository),
  );

  return shellTool;
}
