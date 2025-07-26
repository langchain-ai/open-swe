import {
  MANAGER_GRAPH_ID,
  PLANNER_GRAPH_ID,
  PROGRAMMER_GRAPH_ID,
} from "@open-swe/shared/constants";
import { ThreadStatus } from "@langchain/langgraph-sdk";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

/**
 * UI-specific thread status that extends LangGraph's states
 */
export type ThreadUIStatus =
  | "running" // Maps from LangGraph "busy"
  | "completed" // Business logic: all tasks completed
  | "failed" // UI-specific state
  | "pending" // UI-specific state
  | "idle" // Same as LangGraph "idle"
  | "paused" // Maps from LangGraph "interrupted"
  | "error"; // Same as LangGraph "error"

export function mapLangGraphToUIStatus(status: ThreadStatus): ThreadUIStatus {
  switch (status) {
    case "busy":
      return "running";
    case "interrupted":
      return "paused";
    case "idle":
      return "idle";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

export interface ThreadStatusError {
  message: string;
  type: "not_found" | "unauthorized";
}

export interface ThreadStatusData {
  graph:
    | typeof MANAGER_GRAPH_ID
    | typeof PLANNER_GRAPH_ID
    | typeof PROGRAMMER_GRAPH_ID;
  runId: string;
  threadId: string;
  status: ThreadUIStatus;
  taskPlan?: TaskPlan; // Task plan data when available from programmer sessions
  error?: ThreadStatusError;
}
