import { Dispatch, SetStateAction } from "react";
import { ThreadStatus } from "@langchain/langgraph-sdk";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

// Task with status extended from the agent
export interface TaskWithStatus extends TaskPlan {
  status: ThreadStatus;
  repository?: string;
  date?: string;
}

// Enhanced task type with thread context
export interface TaskWithContext extends TaskWithStatus {
  taskId: string; // Globally unique UUID
  threadId: string; // Internal reference (not exposed to user)
  threadTitle?: string;
  branch?: string;
  createdAt: string; // For chronological sorting
  // status, repository, and date are inherited from TaskWithStatus
}

export interface TaskContextType {
  getTasks: (threadId: string) => Promise<TaskWithStatus[]>;
  getAllTasks: () => Promise<TaskWithContext[]>;
  refreshStatus: () => Promise<void>;
  tasks: TaskWithStatus[];
  setTasks: Dispatch<SetStateAction<TaskWithStatus[]>>;
  allTasks: TaskWithContext[];
  setAllTasks: Dispatch<SetStateAction<TaskWithContext[]>>;
  tasksLoading: boolean;
  setTasksLoading: Dispatch<SetStateAction<boolean>>;
  // Active thread management for real-time status updates
  addActiveThread: (threadId: string) => void;
  removeActiveThread: (threadId: string) => void;
  activeThreads: Set<string>;
}

// Thread summary for grouping tasks
export interface ThreadSummary {
  threadId: string;
  threadTitle: string;
  repository: string;
  branch: string;
  date: string;
  createdAt: string;
  tasks: TaskWithContext[];
  completedTasksCount: number;
  totalTasksCount: number;
  status: ThreadStatus;
}
