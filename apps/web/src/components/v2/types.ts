import { TaskPlan } from "@open-swe/shared/open-swe/types";

export interface ErrorState {
  message: string;
  details?: string;
}

export interface ThreadMetadata {
  id: string;
  title: string;
  lastActivity: string;
  taskCount: number;
  repository: string;
  branch: string;
  taskPlan?: TaskPlan;
  status: string;
  githubIssue?: {
    number: number;
    url: string;
  };
  pullRequest?: {
    status: string;
    url: string;
  };
}

