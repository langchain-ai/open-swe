import { BaseMessage, isAIMessage } from "@langchain/core/messages";
import { createScratchpadFields } from "@open-swe/shared/open-swe/tools";
import z from "zod";

export function getScratchpad(messages: BaseMessage[]): string[] {
  const scratchpadFields = createScratchpadFields("");
  const scratchpad = messages.flatMap((m) => {
    if (!isAIMessage(m)) {
      return [];
    }
    const scratchpadToolCalls = m.tool_calls?.filter(
      (tc) => tc.name === scratchpadFields.name,
    );
    if (!scratchpadToolCalls?.length) {
      return [];
    }
    return scratchpadToolCalls.map(
      (tc) => (tc.args as z.infer<typeof scratchpadFields.schema>).scratchpad,
    );
  });
  return scratchpad.flat();
}
