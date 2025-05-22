import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { applyPatch } from "diff";

const applyDiffToolSchema = z.object({
  diff: z.string().describe("The diff to apply"),
  file_path: z.string().describe("The file path to apply the diff to"),
});

export const applyDiffTool = tool(
  (input) => {
    const { diff, file_path } = input;
    const res = applyPatch(file_path, diff);
    console.log("res", res);
    return `Successfully applied diff to \`${file_path}\``;
  },
  {
    name: "apply_diff",
    description: "Applies a diff to a file.",
    schema: applyDiffToolSchema,
  },
);
