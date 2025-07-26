import useSWR from "swr";
import {
  THREAD_STATUS_SWR_CONFIG,
  TASK_PLAN_SWR_CONFIG,
} from "@/lib/swr-config";
import { ThreadUIStatus, ThreadStatusData } from "@/lib/schemas/thread-status";
import { fetchThreadStatus } from "@/services/thread-status.service";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

interface UseThreadStatusOptions {
  enabled?: boolean;
  refreshInterval?: number;
  useTaskPlanConfig?: boolean;
}

interface ThreadStatusResult {
  status: ThreadUIStatus;
  taskPlan?: TaskPlan;
  isLoading: boolean;
  error: Error | null;
  mutate: () => void;
}

/**
 * Thread status hook using SWR for real-time status updates
 * Uses SWR caching directly instead of manual Zustand cache
 * Can use high-frequency task plan config for real-time progress updates
 */
export function useThreadStatus(
  threadId: string,
  options: UseThreadStatusOptions = {},
): ThreadStatusResult {
  const {
    enabled = true,
    refreshInterval,
    useTaskPlanConfig = false,
  } = options;

  const swrConfig = useTaskPlanConfig
    ? TASK_PLAN_SWR_CONFIG
    : THREAD_STATUS_SWR_CONFIG;

  const finalConfig = refreshInterval
    ? { ...swrConfig, refreshInterval }
    : swrConfig;

  const swrKey = enabled ? `thread-status-${threadId}` : null;

  const { data, error, isLoading, mutate } = useSWR<ThreadStatusData>(
    swrKey,
    () => fetchThreadStatus(threadId),
    finalConfig,
  );

  return {
    status: data?.status || "idle",
    taskPlan: data?.taskPlan,
    isLoading,
    error: data?.error ?? error,
    mutate,
  };
}
