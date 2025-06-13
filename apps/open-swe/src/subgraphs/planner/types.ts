import "@langchain/langgraph/zod";
import { z } from "zod";
import { Messages, messagesStateReducer } from "@langchain/langgraph";
import { BaseMessage } from "@langchain/core/messages";
import { GraphAnnotation } from "@open-swe/shared/open-swe/types";
import { withLangGraph } from "@langchain/langgraph/zod";

export const PlannerGraphStateObj = GraphAnnotation.extend({
  plannerMessages: withLangGraph(
    z.custom<BaseMessage[]>(),
    {
      reducer: {
        schema: z.custom<Messages>(),
        fn: messagesStateReducer,
      },
      jsonSchemaExtra: {
        langgraph_type: "messages",
      },
      default: () => [],
    },
  ),
});

export type PlannerGraphState = z.infer<typeof PlannerGraphStateObj>;
export type PlannerGraphUpdate = Partial<PlannerGraphState>;
