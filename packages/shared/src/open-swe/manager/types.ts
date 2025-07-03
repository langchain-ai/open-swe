import { MessagesZodState } from "@langchain/langgraph";
import { TargetRepository, TaskPlan, AgentSession } from "../types.js";
import { z } from "zod";
import { withLangGraph } from "@langchain/langgraph/zod";

export const ManagerGraphStateObj = MessagesZodState.extend({
  /**
   * The GitHub issue number that the user's request is associated with.
   * If not provided when the graph is invoked, it will create an issue.
   */
  githubIssueId: z.number(),
  /**
   * The GitHub pull request number of the PR which resolves the user's request.
   * If not provided when the graph is invoked, it will create a PR.
   */
  githubPullRequestId: z.number().optional(),
  /**
   * The target repository the request should be executed in.
   */
  targetRepository: z.custom<TargetRepository>(),
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
});

export type ManagerGraphState = z.infer<typeof ManagerGraphStateObj>;
export type ManagerGraphUpdate = Partial<ManagerGraphState>;
