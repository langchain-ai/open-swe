import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { Sandbox } from "@e2b/code-interpreter";
import { GraphConfig } from "../types.js";

const shellToolSchema = z.object({
  command: z.array(z.string()).describe("The command to run"),
  workdir: z
    .string()
    .optional()
    .describe("The working directory for the command."),
  timeout: z
    .number()
    .optional()
    .describe(
      "The maximum time to wait for the command to complete in milliseconds.",
    ),
});

export const shellTool = tool(
  async (input, config: GraphConfig) => {
    const sessionId = config.configurable?.sandbox_session_id;
    if (!sessionId) {
      return "FAILED TO RUN COMMAND: No sandbox session ID provided";
    }

    const sandbox = await Sandbox.connect(sessionId);
    const { command, workdir, timeout } = input;
    const result = await sandbox.commands.run(command.join(" "), {
      timeoutMs: timeout,
      cwd: workdir,
    });

    return `Command exited with code ${result.exitCode}:\n
${result.error ? `Error: ${result.error}\n` : ""}
Stdout:\n${result.stdout}\n\nStderr:\n${result.stderr}`;
  },
  {
    name: "shell",
    description: "Runs a shell command, and returns its output.",
    schema: shellToolSchema,
  },
);
