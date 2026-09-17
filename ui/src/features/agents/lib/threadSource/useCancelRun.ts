import { useCallback } from "react"
import { useQueryClient } from "@tanstack/react-query"

import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  useCancelAgentThread,
} from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"

/**
 * Cancel a thread's live run by thread id. A run started from Slack/Linear/
 * GitHub (or joined after a reload) has no client-side run id to cancel, so
 * this is the only handle that always works.
 *
 * `after` runs once the server accepted the cancellation, for a source that
 * also has a client-side stream to tear down.
 */
export function useCancelRun(
  threadId: string,
  after?: () => Promise<void>
): () => Promise<void> {
  const queryClient = useQueryClient()
  const cancelThread = useCancelAgentThread(threadId)

  return useCallback(async () => {
    let cancelled
    try {
      cancelled = await cancelThread.mutateAsync()
    } catch {
      // Cancellation failed (transient 5xx, or a non-owner viewer). Leave the
      // thread's status polling untouched: presenting a stopped state here
      // would strand the UI on a still-running run.
      return
    }
    await after?.()
    if (cancelled.status === "running") return
    queryClient.setQueryData<AgentThread>(agentThreadKeys.detail(threadId), (prev) =>
      prev ? { ...prev, status: "interrupted" as const } : prev
    )
    invalidateAgentThreadLists(queryClient)
  }, [after, cancelThread, queryClient, threadId])
}
