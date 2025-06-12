import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { Sandbox } from "@daytonaio/sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { daytonaClient } from "../utils/sandbox.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getRepoAbsolutePath } from "../utils/git.js";

const logger = createLogger(LogLevel.INFO, "ShellTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

const createShellToolSchema = (state: GraphState) => {
  const repoRoot = getRepoAbsolutePath(state.targetRepository);
  const shellToolSchema = z.object({
    command: z
      .array(z.string())
      .describe(
        "The command to run. Ensure the command is properly formatted, with arguments in the correct order, and including any wrapping strings, quotes, etc. By default, this command will be executed in the root of the repository, unless a custom workdir is specified.",
      ),
    workdir: z
      .string()
      .default(repoRoot)
      .describe(
        `The working directory for the command. Defaults to the root of the repository (${repoRoot}). You should only specify this if the command you're running can not be executed from the root of the repository.`,
      ),
    timeout: z
      .number()
      .optional()
      .default(TIMEOUT_SEC)
      .describe(
        "The maximum time to wait for the command to complete in seconds.",
      ),
  });
  return shellToolSchema;
};

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
        timeout ?? TIMEOUT_SEC,
      );

      if (response.exitCode !== 0) {
        logger.error("Failed to run command", {
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
        logger.error("Failed to run command", {
          input,
          error: errorFields,
        });
        throw new Error(
          `Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorFields.result}\nStdout:\n${errorFields.artifacts?.stdout}`,
        );
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
    schema: createShellToolSchema(getCurrentTaskInput<GraphState>()),
  },
);
