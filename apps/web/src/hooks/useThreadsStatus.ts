import useSWR from "swr";
import { ThreadUIStatus, ThreadStatusData } from "@/lib/schemas/thread-status";
import { fetchThreadStatus } from "@/services/thread-status.service";
import { THREAD_STATUS_SWR_CONFIG } from "@/lib/swr-config";
import { useMemo, useRef } from "react";
import { Thread } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import { GraphState, TaskPlan } from "@openswe/shared/open-swe/types";

export interface SessionCacheData {
  plannerData?: { thread: Thread<PlannerGraphState> };
  programmerData?: { thread: Thread<GraphState> };
  timestamp: number;
}

export type SessionCache = Map<string, SessionCacheData>;

interface ThreadStatusMap {
  [threadId: string]: ThreadUIStatus;
}

interface TaskPlanMap {
  [threadId: string]: TaskPlan;
}

interface ThreadStatusCounts {
  all: number;
  running: number;
  completed: number;
  failed: number;
  pending: number;
  idle: number;
  paused: number;
  error: number;
}

interface GroupedThreadIds {
  running: string[];
  completed: string[];
  failed: string[];
  pending: string[];
  idle: string[];
  paused: string[];
  error: string[];
}

interface UseThreadsStatusResult {
  statusMap: ThreadStatusMap;
  taskPlanMap: TaskPlanMap;
  statusCounts: ThreadStatusCounts;
  groupedThreads: GroupedThreadIds;
  isLoading: boolean;
  hasErrors: boolean;
}

const sessionDataCache: SessionCache = new Map();

/**
 * Fetches statuses for multiple threads in parallel
 * Uses session caching to achieve "single request per thread + cache sessions" goal
 */
async function fetchAllThreadStatuses(
  threadIds: string[],
  lastPollingStates: Map<string, ThreadStatusData>,
  managerThreads?: Thread<ManagerGraphState>[],
): Promise<{
  statusMap: ThreadStatusMap;
  taskPlanMap: TaskPlanMap;
  updatedStates: Map<string, ThreadStatusData>;
}> {
  const statusPromises = threadIds.map(async (threadId) => {
    try {
      const lastState = lastPollingStates.get(threadId) || null;

      const managerThread = managerThreads?.find(
        (t) => t.thread_id === threadId,
      );

      const statusData = await fetchThreadStatus(
        threadId,
        lastState,
        managerThread,
        sessionDataCache,
      );
      return { threadId, status: statusData.status, statusData };
    } catch (error) {
      console.error(`Failed to fetch status for thread ${threadId}:`, error);
      return {
        threadId,
        status: "idle" as ThreadUIStatus,
        statusData: null,
      };
    }
  });

  const results = await Promise.all(statusPromises);
  const statusMap: ThreadStatusMap = {};
  const taskPlanMap: TaskPlanMap = {};
  const updatedStates = new Map<string, ThreadStatusData>();

  results.forEach(({ threadId, status, statusData }) => {
    statusMap[threadId] = status;
    if (statusData) {
      updatedStates.set(threadId, statusData);
      if (statusData.taskPlan) {
        taskPlanMap[threadId] = statusData.taskPlan;
      }
    }
  });

  return { statusMap, taskPlanMap, updatedStates };
}

/**
 * Hook that fetches statuses for multiple threads in parallel
 * Uses SWR for caching and deduplication with state optimization
 */
export function useThreadsStatus(
  threadIds: string[],
  managerThreads?: Thread<ManagerGraphState>[],
): UseThreadsStatusResult {
  const lastPollingStatesRef = useRef<Map<string, ThreadStatusData>>(new Map());

  const { sortedThreadIds, threadIdsKey } = useMemo(() => {
    const sortedIds = [...threadIds].sort();
    return {
      sortedThreadIds: sortedIds,
      threadIdsKey: sortedIds.join(","),
    };
  }, [threadIds]);

  const totalThreads = sortedThreadIds.length;

  const swrKey = useMemo(() => {
    if (totalThreads === 0) {
      return null;
    }

    if (totalThreads <= 4) {
      return `threads-status-batch-${totalThreads}-${threadIdsKey}`;
    }

    return `threads-status-${threadIdsKey}`;
  }, [threadIdsKey, totalThreads]);

  const {
    data: fetchResult,
    isLoading,
    error,
  } = useSWR(
    swrKey,
    async () => {
      if (process.env.NODE_ENV === "development") {
        console.log(
          `[Status SWR] Fetching statuses for ${threadIds.length} threads`,
        );
      }

      const result = await fetchAllThreadStatuses(
        sortedThreadIds,
        lastPollingStatesRef.current,
        managerThreads,
      );
      lastPollingStatesRef.current = result.updatedStates;
      return result;
    },
    THREAD_STATUS_SWR_CONFIG,
  );

  const statusMap = fetchResult?.statusMap;
  const taskPlanMap = fetchResult?.taskPlanMap;
  const hasErrors = Boolean(error);

  return useMemo(() => {
    const resolvedStatusMap: ThreadStatusMap = statusMap ?? {};
    const resolvedTaskPlanMap: TaskPlanMap = taskPlanMap ?? {};

    const groupedThreads: GroupedThreadIds = {
      running: [],
      completed: [],
      failed: [],
      pending: [],
      idle: [],
      paused: [],
      error: [],
    };

    Object.entries(resolvedStatusMap).forEach(([threadId, status]) => {
      if (groupedThreads[status]) {
        groupedThreads[status].push(threadId);
      }
    });

    const statusCounts: ThreadStatusCounts = {
      all: totalThreads,
      running: groupedThreads.running.length,
      completed: groupedThreads.completed.length,
      failed: groupedThreads.failed.length,
      pending: groupedThreads.pending.length,
      idle: groupedThreads.idle.length,
      paused: groupedThreads.paused.length,
      error: groupedThreads.error.length,
    };

    return {
      statusMap: resolvedStatusMap,
      taskPlanMap: resolvedTaskPlanMap,
      statusCounts,
      groupedThreads,
      isLoading,
      hasErrors,
    };
  }, [statusMap, taskPlanMap, totalThreads, isLoading, hasErrors]);
}
