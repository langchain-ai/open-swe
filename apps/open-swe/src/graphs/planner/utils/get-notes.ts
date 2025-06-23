import { BaseMessage, isAIMessage } from "@langchain/core/messages";
import { createTakePlannerNotesFields } from "@open-swe/shared/open-swe/tools";
import z from "zod";

export function getPlannerNotes(messages: BaseMessage[]): string[] {
  const plannerNotesFields = createTakePlannerNotesFields();
  const plannerNotes = messages.flatMap((m) => {
    if (!isAIMessage(m)) {
      return [];
    }
    const notesToolCalls = m.tool_calls?.filter(
      (tc) => tc.name === plannerNotesFields.name,
    );
    if (!notesToolCalls?.length) {
      return [];
    }
    return notesToolCalls.map(
      (tc) => (tc.args as z.infer<typeof plannerNotesFields.schema>).notes,
    );
  });
  return plannerNotes.flat();
}
