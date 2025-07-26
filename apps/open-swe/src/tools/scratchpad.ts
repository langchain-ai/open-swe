import { tool } from "@langchain/core/tools";
import { createScratchpadFields } from "@open-swe/shared/open-swe/tools";

export function createScratchpadTool(whenMessage: string) {
  const scratchpadTool = tool(
    async (
      _input,
    ): Promise<{ result: string; status: "success" | "error" }> => {
      // TODO: This should write to saved state once that feature is released in LangGraph.
      return {
        result: "Successfully wrote to scratchpad. Thank you!",
        status: "success",
      };
    },
    createScratchpadFields(whenMessage),
  );

  return scratchpadTool;
}
