import { AgentSession } from "@openswe/shared/open-swe/types";

export interface RestartRunRequest {
  managerThreadId: string;
  plannerThreadId: string;
  // Programmer thread ID can be undefined if the error occurred in the planner graph
  programmerThreadId?: string;
}

export interface RestartRunResponse {
  managerSession: AgentSession;
  plannerSession: AgentSession;
  programmerSession: AgentSession;
}
