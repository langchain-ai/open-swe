import "@langchain/langgraph/zod";
import { z } from "zod";
import { MessagesZodState } from "@langchain/langgraph";
import {
  AgentSession,
  CustomRules,
  ModelTokenData,
  TargetRepository,
  TaskPlan,
} from "../types.js";
import { withLangGraph } from "@langchain/langgraph/zod";
import { tokenDataReducer } from "../../caching.js";

export const PlannerGraphStateObj = MessagesZodState.extend({
  sandboxSessionId: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
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
  codebaseTree: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
  }),
  /**
   * Cache of fetched document content keyed by URLs.
   */
  documentCache: withLangGraph(z.custom<Record<string, string>>(), {
    reducer: {
      schema: z.custom<Record<string, string>>(),
      fn: (state, update) => ({ ...state, ...update }),
    },
    default: () => ({}),
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
  contextGatheringNotes: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
    default: () => "",
  }),
  branchName: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
  }),
  planChangeRequest: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
  }),
  programmerSession: withLangGraph(z.custom<AgentSession>(), {
    reducer: {
      schema: z.custom<AgentSession>(),
      fn: (_state, update) => update,
    },
  }),
  proposedPlanTitle: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
    default: () => "",
  }),
  customRules: withLangGraph(z.custom<CustomRules>().optional(), {
    reducer: {
      schema: z.custom<CustomRules>().optional(),
      fn: (_state, update) => update,
    },
  }),
  autoAcceptPlan: withLangGraph(z.custom<boolean>().optional(), {
    reducer: {
      schema: z.custom<boolean>().optional(),
      fn: (_state, update) => update,
    },
  }),
  tokenData: withLangGraph(z.custom<ModelTokenData[]>().optional(), {
    reducer: {
      schema: z
        .custom<
          ModelTokenData[] | { data: ModelTokenData[]; replaceMode: boolean }
        >()
        .optional(),
      fn: (state, update) => {
        const typedState = state as ModelTokenData[] | undefined;
        // Check if update contains a replace flag
        if (
          update &&
          typeof update === "object" &&
          "replaceMode" in update &&
          "data" in update
        ) {
          const typedUpdate = update as {
            data: ModelTokenData[];
            replaceMode: boolean;
          };
          return tokenDataReducer(
            typedState,
            typedUpdate.data,
            typedUpdate.replaceMode,
          );
        }
        // Default behavior - merge mode
        return tokenDataReducer(typedState, (update || []) as ModelTokenData[]);
      },
    },
  }),
});

export type PlannerGraphState = z.infer<typeof PlannerGraphStateObj>;

// Custom update type that supports the special tokenData format
export type PlannerGraphUpdate = Partial<
  Omit<PlannerGraphState, "tokenData">
> & {
  tokenData?: ModelTokenData[] | { data: ModelTokenData[]; replaceMode: boolean };
};

