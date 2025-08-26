import "@langchain/langgraph/zod";
import { z } from "zod";
import {
  Messages,
  messagesStateReducer,
  MessagesZodState,
} from "@langchain/langgraph";
import {
  CustomRules,
  ModelTokenData,
  TargetRepository,
  TaskPlan,
} from "../types.js";
import { withLangGraph } from "@langchain/langgraph/zod";
import { BaseMessage } from "@langchain/core/messages";
import { tokenDataReducer } from "../../caching.js";

export const ReviewerGraphStateObj = MessagesZodState.extend({
  /**
   * We must include the internal messages so that the reviewer has an
   * accurate picture of the conversation.
   */
  internalMessages: withLangGraph(z.custom<BaseMessage[]>(), {
    reducer: {
      schema: z.custom<Messages>(),
      fn: messagesStateReducer,
    },
    jsonSchemaExtra: {
      langgraph_type: "messages",
    },
    default: () => [],
  }),
  /**
   * A separate list of messages for the reviewer. Used to track both
   * internal messages which do not need to be shown to the user/propagated
   * back to the programmer, and to determine how many reviewer actions have
   * been executed.
   */
  reviewerMessages: withLangGraph(z.custom<BaseMessage[]>(), {
    reducer: {
      schema: z.custom<Messages>(),
      fn: messagesStateReducer,
    },
    jsonSchemaExtra: {
      langgraph_type: "messages",
    },
    default: () => [],
  }),
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
  taskPlan: withLangGraph(z.custom<TaskPlan>(), {
    reducer: {
      schema: z.custom<TaskPlan>(),
      fn: (_state, update) => update,
    },
  }),
  branchName: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
  }),
  baseBranchName: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
  }),
  changedFiles: withLangGraph(z.string(), {
    reducer: {
      schema: z.string(),
      fn: (_state, update) => update,
    },
  }),
  customRules: withLangGraph(z.custom<CustomRules>().optional(), {
    reducer: {
      schema: z.custom<CustomRules>().optional(),
      fn: (_state, update) => update,
    },
  }),
  dependenciesInstalled: withLangGraph(z.boolean(), {
    reducer: {
      schema: z.boolean(),
      fn: (_state, update) => update,
    },
  }),
  /**
   * The number of times the reviewer subgraph has been executed.
   */
  reviewsCount: withLangGraph(z.custom<number>(), {
    reducer: {
      schema: z.custom<number>(),
      fn: (_state, update) => update,
    },
    default: () => 0,
  }),
  tokenData: withLangGraph(z.custom<ModelTokenData[]>().optional(), {
    reducer: {
      schema: z.custom<ModelTokenData[]>().optional(),
      fn: tokenDataReducer,
    },
  }),
});

export type ReviewerGraphState = z.infer<typeof ReviewerGraphStateObj>;
export type ReviewerGraphUpdate = Partial<ReviewerGraphState>;
