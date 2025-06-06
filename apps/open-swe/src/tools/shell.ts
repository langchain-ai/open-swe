import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { Sandbox } from "@daytonaio/sdk";
import { GraphState } from "../types.js";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { daytonaClient } from "../utils/sandbox.js";
import { SANDBOX_ROOT_DIR } from "../constants.js";

const logger = createLogger(LogLevel.INFO, "ShellTool");

const DEFAULT_COMMAND_TIMEOUT = 60_000; // 1 minute

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

const shellToolSchema = z.object({
  command: z.array(z.string()).describe("The command to run"),
  workdir: z
    .string()
    .default(SANDBOX_ROOT_DIR)
    .describe(
      `The working directory for the command. Ensure this path is NOT included in any command arguments, as it will be added automatically. Defaults to '${SANDBOX_ROOT_DIR}' as this is the root directory of the sandbox.`,
    ),
  timeout: z
    .number()
    .optional()
    .default(DEFAULT_COMMAND_TIMEOUT)
    .describe(
      "The maximum time to wait for the command to complete in milliseconds.",
    ),
});

export const shellTool = tool(
  async (input): Promise<{ result: string; status: "success" | "error" }> => {
    let sandbox: Sandbox | undefined;
    try {
      const state = getCurrentTaskInput<GraphState>();
      const { sandboxSessionId } = state;
      if (!sandboxSessionId) {
        logger.error("FAILED TO RUN COMMAND: No sandbox session ID provided", {
          input,
        });
        throw new Error(
          "FAILED TO RUN COMMAND: No sandbox session ID provided",
        );
      }

      sandbox = await daytonaClient().get(sandboxSessionId);
      const { command, workdir, timeout } = input;
      const response = await sandbox.process.executeCommand(
        command.join(" "),
        workdir,
        DEFAULT_ENV,
        timeout ?? DEFAULT_COMMAND_TIMEOUT,
      );

      if (response.exitCode !== 0) {
        logger.error("Failed to run command", {
          error: response.result,
          error_result: response,
          input,
        });
        throw new Error(`Command failed. Exit code: ${response.exitCode}\nResult: ${response.result}\nStdout:\n${response.artifacts?.stdout}`)
      }

      return {
        result: response.result,
        status: "success",
      };
    } catch (e) {
      const errorFields = getSandboxErrorFields(e);
      if (errorFields) {
        logger.error("Failed to run command", {
          input,
          error: errorFields,
        });
        throw new Error(`Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorFields.result}\nStdout:\n${errorFields.artifacts?.stdout}`)
      }

      logger.error(
        "Failed to run command: " +
          (e instanceof Error ? e.message : "Unknown error"),
        {
          error: e,
          input,
        },
      );
      throw new Error(
        "FAILED TO RUN COMMAND: " +
          (e instanceof Error ? e.message : "Unknown error"),
      );
    }
  },
  {
    name: "shell",
    description: "Runs a shell command, and returns its output.",
    schema: shellToolSchema,
  },
);
