import { Thread } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { ThreadMetadata } from "@/components/v2/types";
import { useThreadStatus } from "./useThreadStatus";
import { useMemo } from "react";
import { getThreadTitle, computeThreadTitle } from "@/lib/thread";
import { calculateLastActivity } from "@/lib/thread-utils";
import { ThreadStatusError } from "@/lib/schemas/thread-status";

/**
 * Hook that combines thread metadata with real-time status
 */
export function useThreadMetadata(thread: Thread<ManagerGraphState>): {
  metadata: ThreadMetadata;
  isStatusLoading: boolean;
  statusError: Error | ThreadStatusError | null;
} {
  const {
    status,
    isLoading: isStatusLoading,
    error: statusError,
    taskPlan: realTimeTaskPlan,
  } = useThreadStatus(thread.thread_id);

  const metadata: ThreadMetadata = useMemo((): ThreadMetadata => {
    const values = thread.values;

    return {
      id: thread.thread_id,
      title: computeThreadTitle(realTimeTaskPlan, getThreadTitle(thread)),
      lastActivity: calculateLastActivity(thread.updated_at),
      taskCount: realTimeTaskPlan?.tasks.length ?? 0,
      repository: values?.targetRepository
        ? `${values.targetRepository.owner}/${values.targetRepository.repo}`
        : "",
      branch: values?.targetRepository?.branch || "main",
      taskPlan: realTimeTaskPlan,
      status,
      githubIssue: values?.githubIssueId
        ? {
            number: values?.githubIssueId,
            url: `https://github.com/${values?.targetRepository?.owner}/${values?.targetRepository?.repo}/issues/${values?.githubIssueId}`,
          }
        : undefined,
    };
  }, [thread, status, realTimeTaskPlan]);

  return {
    metadata,
    isStatusLoading,
    statusError,
  };
}
