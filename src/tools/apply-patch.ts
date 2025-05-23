import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { applyPatch } from "diff";
import { GraphState } from "../types.js";
import { Sandbox } from "@e2b/code-interpreter";
import { readFile, writeFile } from "../utils/read-write.js";
import { getCurrentTaskInput } from "@langchain/langgraph";

const applyPatchToolSchema = z.object({
  diff: z.string().describe("The diff to apply. Use a standard diff format."),
  file_path: z.string().describe("The file path to apply the diff to."),
});

export const applyPatchTool = tool(
  async (input) => {
    const state = getCurrentTaskInput<GraphState>();
    const { sandboxSessionId } = state;
    if (!sandboxSessionId) {
      console.error("FAILED TO RUN COMMAND: No sandbox session ID provided", {
        input,
      });
      throw new Error("FAILED TO RUN COMMAND: No sandbox session ID provided");
    }

    const { diff, file_path } = input;

    const sandbox = await Sandbox.connect(sandboxSessionId);

    const { success: readFileSuccess, output: readFileOutput } = await readFile(
      sandbox,
      file_path,
    );
    if (!readFileSuccess) {
      return readFileOutput;
    }

    console.log(`\nApplying patch to file ${file_path}\n`);
    console.log("\nreadFileOutput\n", readFileOutput);
    console.log("\ndiff\n", diff);

    let patchedContent: string | false;
    try {
      patchedContent = applyPatch(readFileOutput, diff);
    } catch (e) {
      console.error("Failed to apply patch", e);
      return `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'. This may be due to an invalid diff format or conflicting changes with the file's current content. Original content length: ${readFileOutput.length}, Diff: ${diff.substring(0, 100)}...`;
    }

    if (patchedContent === false) {
      return `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'. This may be due to an invalid diff format or conflicting changes with the file's current content. Original content length: ${readFileOutput.length}, Diff: ${diff.substring(0, 100)}...`;
    }

    // TODO: Should we be committing every time we apply a diff?
    const { success: writeFileSuccess, output: writeFileOutput } =
      await writeFile(sandbox, file_path, patchedContent);
    if (!writeFileSuccess) {
      return writeFileOutput;
    }

    return `Successfully applied diff to \`${file_path}\` and saved changes.`;
  },
  {
    name: "apply_patch",
    description: "Applies a diff to a file given a file path and diff content.",
    schema: applyPatchToolSchema,
  },
);
