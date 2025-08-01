import { tool } from "@langchain/core/tools";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createShellToolFields } from "@open-swe/shared/open-swe/tools";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { Sandbox } from "@daytonaio/sdk";
import { createShellExecutor } from "../utils/shell-executor/index.js";

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

        // Get sandbox if needed for sandbox mode
        let sandbox: Sandbox | undefined;
        if (!isLocalMode(config)) {
          sandbox = await getSandboxSessionOrThrow(input);
        }

        const executor = createShellExecutor(config);
        const response = await executor.executeCommand({
          command,
          workdir,
          timeout: timeout ?? TIMEOUT_SEC,
          env: DEFAULT_ENV,
        });

        return {
          result: response.result ?? `exit code: ${response.exitCode}`,
          status: "success",
        };
      } catch (error: any) {
        const errorFields = getSandboxErrorFields(error);
        if (errorFields) {
          return {
            result: `Error: ${errorFields.result ?? errorFields.artifacts?.stdout}`,
            status: "error",
          };
        }

        return {
          result: `Error: ${error.message || String(error)}`,
          status: "error",
        };
      }
    },
    createShellToolFields(state.targetRepository),
  );

  return shellTool;
}
