import { tool } from "@langchain/core/tools";
import { createTakePlannerNotesFields } from "@open-swe/shared/open-swe/tools";

export function createPlannerNotesTool() {
  const plannerNotesTool = tool(
    async (
      _input,
    ): Promise<{ result: string; status: "success" | "error" }> => {
      // TODO: This should write to saved state once that feature is released in LangGraph.
      return {
        result: "Successfully saved notes. Thank you!",
        status: "success",
      };
    },
    createTakePlannerNotesFields(),
  );

  return plannerNotesTool;
}
