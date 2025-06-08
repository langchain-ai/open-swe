import "@langchain/langgraph/zod";
import { z } from "zod";
import { addMessages, Messages } from "@langchain/langgraph";
import { BaseMessage } from "@langchain/core/messages";
import { GraphAnnotation } from "@open-swe/shared/open-swe/types";

export const PlannerGraphStateObj = GraphAnnotation.extend({
  plannerMessages: z
    .custom<BaseMessage[]>()
    .default(() => [])
    .langgraph.reducer<Messages>((state, update) => addMessages(state, update)),
});

export type PlannerGraphState = z.infer<typeof PlannerGraphStateObj>;
export type PlannerGraphUpdate = Partial<PlannerGraphState>;
