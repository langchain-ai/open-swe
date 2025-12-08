import { MessagesZodState } from "@langchain/langgraph";
import { TargetRepository, TaskPlan, AgentSession } from "../types.js";
import type { FeatureGraph } from "../../feature-graph/graph.js";
import { z } from "zod";
import { withLangGraph } from "@langchain/langgraph/zod";

export const FeatureProposalSchema = z.object({
  proposalId: z.string(),
  featureId: z.string(),
  summary: z.string(),
  status: z.enum(["proposed", "approved", "rejected"]),
  rationale: z.string().optional(),
  updatedAt: z.string(),
});

export const FeatureProposalStateSchema = z.object({
  proposals: z.array(FeatureProposalSchema),
  activeProposalId: z.string().optional(),
});

export const ManagerGraphStateObj = MessagesZodState.extend({
  /**
   * The target repository the request should be executed in.
   */
  targetRepository: z.custom<TargetRepository>(),
  /**
   * Absolute path to the user's selected workspace when running locally.
   */
  workspaceAbsPath: z.string().optional(),
  /**
   * Resolved workspace path inside the container after validation.
   */
  workspacePath: withLangGraph(z.string().optional(), {
    reducer: {
      schema: z.string().optional(),
      fn: (_state, update) => update,
    },
  }),
  issueId: z.number().optional(),
  /**
   * The tasks generated for this request.
   */
  taskPlan: z.custom<TaskPlan>(),
  /**
   * The programmer session
   */
  programmerSession: z.custom<AgentSession>().optional(),
  /**
   * The planner session
   */
  plannerSession: z.custom<AgentSession>().optional(),
  /**
   * The branch name to checkout and make changes on.
   * Can be user specified, or defaults to `open-swe/<manager-thread-id>
   */
  branchName: z.string(),
  /**
   * Whether or not to auto accept the generated plan.
   */
  autoAcceptPlan: withLangGraph(z.custom<boolean>().optional(), {
    reducer: {
      schema: z.custom<boolean>().optional(),
      fn: (_state, update) => update,
    },
  }),
  /**
   * Handle to the feature graph declared within the target workspace.
   */
  featureGraph: z.custom<FeatureGraph>().optional(),
  /**
   * Feature identifiers that should be considered active for the current run.
   */
  activeFeatureIds: z.array(z.string()).optional(),
  /**
   * Tracks whether the user has approved the active feature selection.
   */
  userHasApprovedFeature: withLangGraph(z.custom<boolean>().optional(), {
    reducer: {
      schema: z.custom<boolean>().optional(),
      fn: (state, update) => update ?? state,
    },
  }),
  /**
   * Tracks feature proposals and their lifecycle state across turns.
   */
  featureProposals: FeatureProposalStateSchema.optional(),
});

export type ManagerGraphState = z.infer<typeof ManagerGraphStateObj>;
export type ManagerGraphUpdate = Partial<ManagerGraphState>;
export type FeatureProposal = z.infer<typeof FeatureProposalSchema>;
export type FeatureProposalState = z.infer<typeof FeatureProposalStateSchema>;
