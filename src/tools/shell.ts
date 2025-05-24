import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { Sandbox } from "@e2b/code-interpreter";
import { GraphState } from "../types.js";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { TIMEOUT_MS } from "../constants.js";

const DEFAULT_COMMAND_TIMEOUT = 120_000; // 2 minutes

const shellToolSchema = z.object({
  command: z.array(z.string()).describe("The command to run"),
  workdir: z
    .string()
    .optional()
    .describe("The working directory for the command."),
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
        console.error("FAILED TO RUN COMMAND: No sandbox session ID provided", {
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
        console.error("Failed to run command", {
          error: result.error,
          error_result: result,
          input,
        });
        return `Command failed. Exit code: ${result.exitCode}\nError: ${result.error}\nStderr:\n${result.stderr}`;
      }

      return result.stdout;
    } catch (e: any) {
      if (typeof e === "object" && "result" in e && e.result) {
        console.error("Failed to run command", {
          error: e.message,
          error_result: e.result,
          input,
        });
        return (
          "FAILED TO RUN COMMAND: " +
          e.message +
          "\n" +
          JSON.stringify(e.result, null, 2)
        );
      }

      console.error("Failed to run command: " + e.message, {
        error: e,
        input,
      });
      throw new Error("FAILED TO RUN COMMAND: " + e.message);
    }
  },
  {
    name: "shell",
    description: "Runs a shell command, and returns its output.",
    schema: shellToolSchema,
  },
);
