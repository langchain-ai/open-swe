import "@langchain/langgraph/zod";
import { z } from "zod";
import { MessagesZodState } from "@langchain/langgraph";
import { TargetRepository, TaskPlan } from "@open-swe/shared/open-swe/types";
import { withLangGraph } from "@langchain/langgraph/zod";

export const PlannerGraphStateObj = MessagesZodState.extend({
  sandboxSessionId: z.string().optional(),
  targetRepository: withLangGraph(z.custom<TargetRepository>(), {
    reducer: {
      schema: z.custom<TargetRepository>(),
      fn: (_state, update) => update,
    },
  }),
  githubIssueId: z.number(),
  codebaseTree: z.string().optional(),
  taskPlan: withLangGraph(z.custom<TaskPlan>(), {
    reducer: {
      schema: z.custom<TaskPlan>(),
      fn: (_state, update) => update,
    },
  }),
  proposedPlan: withLangGraph(z.custom<string[]>(), {
    reducer: {
      schema: z.custom<string[]>(),
      fn: (_state, update) => update,
    },
    default: (): string[] => [],
  }),
  planContextSummary: withLangGraph(z.custom<string>(), {
    reducer: {
      schema: z.custom<string>(),
      fn: (_state, update) => update,
    },
    default: () => "",
  }),
  branchName: z.string(),
});

export type PlannerGraphState = z.infer<typeof PlannerGraphStateObj>;
export type PlannerGraphUpdate = Partial<PlannerGraphState>;
