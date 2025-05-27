import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { applyPatch } from "diff";
import { GraphState } from "../types.js";
import { Sandbox } from "@e2b/code-interpreter";
import { readFile, writeFile } from "../utils/read-write.js";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { fixGitPatch } from "../utils/diff.js";
import { createLogger, LogLevel } from "../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "ApplyPatchTool");

const applyPatchToolSchema = z.object({
  diff: z.string().describe("The diff to apply. Use a standard diff format."),
  file_path: z.string().describe("The file path to apply the diff to."),
  workdir: z
    .string()
    .default("/home/user")
    .describe(
      "The working directory for the command. Ensure this path is NOT included in any command arguments, as it will be added automatically. Defaults to '/home/user' as this is the root directory of the sandbox.",
    ),
});

export const applyPatchTool = tool(
  async (input) => {
    const state = getCurrentTaskInput<GraphState>();
    const { sandboxSessionId } = state;
    if (!sandboxSessionId) {
      logger.error("FAILED TO RUN COMMAND: No sandbox session ID provided", {
        input,
      });
      throw new Error("FAILED TO RUN COMMAND: No sandbox session ID provided");
    }

    const { diff, file_path, workdir } = input;

    const sandbox = await Sandbox.connect(sandboxSessionId);

    const { success: readFileSuccess, output: readFileOutput } = await readFile(
      sandbox,
      file_path,
      {
        workDir: workdir,
      },
    );
    if (!readFileSuccess) {
      logger.error("Failed to read file", readFileOutput);
      return readFileOutput;
    }

    let patchedContent: string | false;
    try {
      logger.info(`Applying patch to file ${file_path}`);
      const fixedDiff = fixGitPatch(diff, {
        [file_path]: readFileOutput,
      });
      patchedContent = applyPatch(readFileOutput, fixedDiff);
    } catch (e) {
      logger.error("Failed to apply patch", {
        ...(e instanceof Error
          ? { name: e.name, message: e.message, stack: e.stack }
          : { error: e }),
      });
      const errMessage = e instanceof Error ? e.message : "Unknown error";
      return `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'.\n\nError: ${errMessage}`;
    }

    if (patchedContent === false) {
      logger.error(
        `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'. This may be due to an invalid diff format or conflicting changes with the file's current content. Original content length: ${readFileOutput.length}, Diff: ${diff.substring(0, 100)}...`,
      );
      return `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'. This may be due to an invalid diff format or conflicting changes with the file's current content. Original content length: ${readFileOutput.length}, Diff: ${diff.substring(0, 100)}...`;
    }

    const { success: writeFileSuccess, output: writeFileOutput } =
      await writeFile(sandbox, file_path, patchedContent, {
        workDir: workdir,
      });
    if (!writeFileSuccess) {
      logger.error("Failed to write file", {
        writeFileOutput,
      });
      return writeFileOutput;
    }

    logger.info(
      `Successfully applied diff to \`${file_path}\` and saved changes.`,
    );
    return `Successfully applied diff to \`${file_path}\` and saved changes.`;
  },
  {
    name: "apply_patch",
    description: "Applies a diff to a file given a file path and diff content.",
    schema: applyPatchToolSchema,
  },
);
