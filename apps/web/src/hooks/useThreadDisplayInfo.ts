import { Thread } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { ThreadDisplayInfo, threadToMetadata } from "@/components/v2/types";
import { useThreadStatus } from "./useThreadStatus";
import { useMemo } from "react";

/**
 * Hook that combines thread metadata with real-time status
 */
export function useThreadDisplayInfo(thread: Thread<ManagerGraphState>): {
  displayInfo: ThreadDisplayInfo;
  isStatusLoading: boolean;
  statusError: Error | null;
} {
  const metadata = useMemo(() => threadToMetadata(thread), [thread]);

  const {
    status,
    isLoading: isStatusLoading,
    error: statusError,
  } = useThreadStatus(thread.thread_id);

  const displayInfo: ThreadDisplayInfo = useMemo(
    () => ({
      ...metadata,
      status,
    }),
    [metadata, status],
  );

  return {
    displayInfo,
    isStatusLoading,
    statusError,
  };
}
