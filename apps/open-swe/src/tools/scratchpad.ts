import { tool } from "@langchain/core/tools";
import { getStore } from "@langchain/langgraph";
import { createScratchpadFields } from "@openswe/shared/open-swe/tools";

export async function writeScratchpad(input: {
  scratchpad: string[];
}): Promise<{ result: string; status: "success" | "error" }> {
  const store = getStore();
  if (!store) {
    return {
      result: "Unable to access scratchpad store.",
      status: "error",
    };
  }

  const existing = await store.get(["scratchpad"], "notes");
  const previousNotes = (existing?.value?.notes as string[] | undefined) ?? [];

  await store.put(["scratchpad"], "notes", {
    notes: [...previousNotes, ...input.scratchpad],
  });

  return {
    result: "Successfully wrote to scratchpad. Thank you!",
    status: "success",
  };
}

export function createScratchpadTool(whenMessage: string) {
  const scratchpadTool = tool(
    writeScratchpad,
    createScratchpadFields(whenMessage),
  );

  return scratchpadTool;
}
