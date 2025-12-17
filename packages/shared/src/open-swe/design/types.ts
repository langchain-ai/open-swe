import { MessagesZodState } from "@langchain/langgraph";
import { z } from "zod";
import { withLangGraph } from "@langchain/langgraph/zod";
import { TargetRepository, AgentSession } from "../types.js";
import { FeatureGraph } from "../../feature-graph/graph.js";
import type { FeatureNode, FeatureEdge } from "../../feature-graph/types.js";

/**
 * Represents a pending change to the feature graph that requires user approval.
 */
export const FeatureChangeProposalSchema = z.object({
  id: z.string(),
  type: z.enum(["create", "update", "delete", "connect", "disconnect"]),
  featureId: z.string(),
  targetFeatureId: z.string().optional(),
  summary: z.string(),
  rationale: z.string().optional(),
  impact: z.array(z.string()).optional(),
  status: z.enum(["pending", "approved", "rejected"]),
  createdAt: z.string(),
  updatedAt: z.string(),
});

/**
 * Represents a clarifying question the agent needs answered before proceeding.
 */
export const ClarifyingQuestionSchema = z.object({
  id: z.string(),
  question: z.string(),
  context: z.string().optional(),
  relatedFeatureIds: z.array(z.string()).optional(),
  options: z.array(z.string()).optional(),
  status: z.enum(["pending", "answered", "skipped"]),
  answer: z.string().optional(),
  createdAt: z.string(),
});

/**
 * Tracks the overall design session state.
 */
export const DesignSessionStateSchema = z.object({
  phase: z.enum(["exploring", "designing", "refining", "ready_for_development"]),
  designGoal: z.string().optional(),
  conversationSummary: z.string().optional(),
  lastActivity: z.string(),
});

const isIterable = (value: unknown): value is Iterable<unknown> =>
  typeof value === "object" &&
  value !== null &&
  typeof (value as Iterable<unknown>)[Symbol.iterator] === "function";

/**
 * State schema for the dedicated Design Thread.
 * This thread is isolated from the manager and planner threads to prevent
 * "thread busy" errors and enable focused feature graph design conversations.
 */
export const DesignGraphStateObj = MessagesZodState.extend({
  /**
   * The target repository context.
   */
  targetRepository: z.custom<TargetRepository>(),

  /**
   * Resolved workspace path for feature graph persistence.
   */
  workspacePath: withLangGraph(z.string().optional(), {
    reducer: {
      schema: z.string().optional(),
      fn: (_state, update) => update ?? _state,
    },
  }),

  /**
   * Reference to the parent manager thread for handoff.
   */
  managerThreadId: z.string().optional(),

  /**
   * The current feature graph being designed.
   */
  featureGraph: withLangGraph<
    FeatureGraph | undefined,
    FeatureGraph | undefined,
    z.ZodType<FeatureGraph | undefined>
  >(z.custom<FeatureGraph>((value) => value instanceof FeatureGraph).optional(), {
    reducer: {
      schema: z.custom<FeatureGraph | undefined>((value) =>
        value === undefined || value instanceof FeatureGraph,
      ),
      fn: (state, update) => {
        if (!update) return state;
        if (!(update instanceof FeatureGraph)) return state;
        return update;
      },
    },
  }),

  /**
   * Feature IDs that have been marked as ready for development.
   */
  readyFeatureIds: withLangGraph(z.array(z.string()).optional(), {
    reducer: {
      schema: z.custom<Iterable<unknown> | undefined>(),
      fn: (state, update) => {
        if (update === undefined || update === null) return state;
        if (!isIterable(update) || typeof update === "string") return state;

        const normalized: string[] = [];
        for (const value of update) {
          if (typeof value === "string") {
            normalized.push(value);
          }
        }

        return normalized;
      },
    },
  }),

  /**
   * Pending change proposals awaiting user approval.
   */
  pendingProposals: z.array(FeatureChangeProposalSchema).optional(),

  /**
   * Clarifying questions the agent needs answered.
   */
  clarifyingQuestions: z.array(ClarifyingQuestionSchema).optional(),

  /**
   * Design session metadata.
   */
  designSession: DesignSessionStateSchema.optional(),

  /**
   * History of all approved changes for audit trail.
   */
  changeHistory: z.array(z.object({
    proposalId: z.string(),
    action: z.string(),
    timestamp: z.string(),
    summary: z.string(),
  })).optional(),

  /**
   * Impact analysis results for proposed changes.
   */
  impactAnalysis: z.record(z.string(), z.object({
    affectedFeatures: z.array(z.string()),
    severity: z.enum(["none", "low", "medium", "high"]),
    description: z.string(),
  })).optional(),
});

export type DesignGraphState = z.infer<typeof DesignGraphStateObj>;
export type DesignGraphUpdate = Partial<DesignGraphState>;
export type FeatureChangeProposal = z.infer<typeof FeatureChangeProposalSchema>;
export type ClarifyingQuestion = z.infer<typeof ClarifyingQuestionSchema>;
export type DesignSessionState = z.infer<typeof DesignSessionStateSchema>;

/**
 * Input for starting a new design thread.
 */
export interface DesignThreadInput {
  targetRepository: TargetRepository;
  workspacePath?: string;
  managerThreadId?: string;
  initialPrompt?: string;
  existingGraph?: FeatureGraph;
}

/**
 * Result of a design handoff to planner.
 */
export interface DesignHandoffResult {
  plannerThreadId: string;
  runId: string;
  featureIds: string[];
  featureGraph: FeatureGraph;
}

/**
 * Impact analysis request for a proposed change.
 */
export interface ImpactAnalysisRequest {
  proposalId: string;
  featureId: string;
  changeType: FeatureChangeProposal["type"];
  targetFeatureId?: string;
}
