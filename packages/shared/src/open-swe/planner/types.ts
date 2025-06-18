import "@langchain/langgraph/zod";
import { z } from "zod";
import { MessagesZodState } from "@langchain/langgraph";
import { TargetRepository, TaskPlan } from "../types.js";
import { withLangGraph } from "@langchain/langgraph/zod";

export const PlannerGraphStateObj = MessagesZodState.extend({
  sandboxSessionId: withLangGraph(z.custom<string>(), {
    reducer: {
      schema: z.custom<string>(),
      fn: (_state, update) => update,
    },
  }),
  targetRepository: withLangGraph(z.custom<TargetRepository>(), {
    reducer: {
      schema: z.custom<TargetRepository>(),
      fn: (_state, update) => update,
    },
  }),
  githubIssueId: withLangGraph(z.custom<number>(), {
    reducer: {
      schema: z.custom<number>(),
      fn: (_state, update) => update,
    },
  }),
  codebaseTree: withLangGraph(z.custom<string>(), {
    reducer: {
      schema: z.custom<string>(),
      fn: (_state, update) => update,
    },
  }),
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
  branchName: withLangGraph(z.custom<string>(), {
    reducer: {
      schema: z.custom<string>(),
      fn: (_state, update) => update,
    },
  }),
  planChangeRequest: withLangGraph(z.custom<string>(), {
    reducer: {
      schema: z.custom<string>(),
      fn: (_state, update) => update,
    },
  }),
  programmerThreadId: withLangGraph(z.custom<string>(), {
    reducer: {
      schema: z.custom<string>(),
      fn: (_state, update) => update,
    },
  }),
});

export type PlannerGraphState = z.infer<typeof PlannerGraphStateObj>;
export type PlannerGraphUpdate = Partial<PlannerGraphState>;
