import { useEffect, useRef } from "react"

import type { AgentThread } from "@/features/agents/lib/types"
import { showRunNotification } from "@/lib/notifications"

const TERMINAL_STATUSES = new Set(["finished", "error", "interrupted"])

/** Says what each run did once it ends outside the visible thread, except Slack threads and workers. */
export function useRunCompletionNotifier(
  threads: Array<AgentThread> | undefined,
  activeThreadId: string | undefined,
  openThread: (threadId: string) => void
) {
  const prevThreadsRef = useRef<Map<string, AgentThread>>(new Map())

  useEffect(() => {
    if (!threads) return
    const isViewingThread =
      !!activeThreadId && document.visibilityState === "visible"
    const prev = prevThreadsRef.current
    for (const thread of threads) {
      const previous = prev.get(thread.id)
      // Keep the snapshot from when the run started, so a PR it opened mid-run still reads as new.
      if (previous?.status !== "running" || thread.status !== "running") {
        prev.set(thread.id, thread)
      }
      if (previous === undefined) continue
      if (
        thread.source !== "slack" &&
        thread.taskMembership?.role !== "worker" &&
        previous.status === "running" &&
        TERMINAL_STATUSES.has(thread.status) &&
        !(isViewingThread && thread.id === activeThreadId)
      ) {
        showRunNotification(previous, thread, openThread)
      }
    }
  }, [threads, activeThreadId, openThread])
}
