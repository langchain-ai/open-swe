import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { Sandbox } from "@e2b/code-interpreter";
import { GraphState } from "../types.js";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { TIMEOUT_MS } from "../constants.js";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "ShellTool");

const DEFAULT_COMMAND_TIMEOUT = 60_000; // 1 minute

const shellToolSchema = z.object({
  workdir: z
    .string()
    .optional()
    .describe(
      "The working directory for the command. Ensure this path is NOT included in any command arguments, as it will be added automatically.",
    ),
  command: z.array(z.string()).describe("The command to run"),
  timeout: z
    .number()
    .optional()
    .default(DEFAULT_COMMAND_TIMEOUT)
    .describe(
      "The maximum time to wait for the command to complete in milliseconds.",
    ),
});

export const shellTool = tool(
  async (input) => {
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

      const sandbox = await Sandbox.connect(sandboxSessionId);
      const { command, workdir, timeout } = input;
      const result = await sandbox.commands.run(command.join(" "), {
        timeoutMs: timeout ?? DEFAULT_COMMAND_TIMEOUT,
        cwd: workdir,
      });
      // Add an extra 5 min timeout to the sandbox.
      await sandbox.setTimeout(TIMEOUT_MS);

      if (result.error) {
        logger.error("Failed to run command", {
          error: result.error,
          error_result: result,
          input,
        });
        return `Command failed. Exit code: ${result.exitCode}\nError: ${result.error}\nStderr:\n${result.stderr}`;
      }

      return result.stdout;
    } catch (e) {
      const errorFields = getSandboxErrorFields(e);
      if (errorFields) {
        logger.error("Failed to run command", {
          input,
          error: errorFields,
        });
        return `Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorFields.error}\nStderr:\n${errorFields.stderr}\nStdout:\n${errorFields.stdout}`;
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
