import { ThreadUIStatus } from "@/lib/schemas/thread-status";
import { TaskPlan } from "@openswe/shared/open-swe/types";

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
  status: ThreadUIStatus;
  pullRequest?: {
    number: number;
    url: string;
    status: "draft" | "open" | "merged" | "closed";
  };
}
