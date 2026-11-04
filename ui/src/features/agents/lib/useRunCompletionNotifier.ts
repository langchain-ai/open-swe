import { useEffect, useRef } from "react"

import type { AgentThread } from "@/features/agents/lib/types"
import { showRunNotification } from "@/lib/notifications"

const TERMINAL_STATUSES = new Set(["finished", "error", "interrupted"])

/** Notifies on completion outside the visible thread, except Slack threads and workers. */
export function useRunCompletionNotifier(
  threads: Array<AgentThread> | undefined,
  activeThreadId: string | undefined,
  openThread: (threadId: string) => void
) {
  const prevStatusRef = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    if (!threads) return
    const isViewingThread =
      !!activeThreadId && document.visibilityState === "visible"
    const prev = prevStatusRef.current
    for (const thread of threads) {
      const prevStatus = prev.get(thread.id)
      const status = thread.waitingForDelegatedWork ? "running" : thread.status
      if (prevStatus === undefined) {
        prev.set(thread.id, status)
        continue
      }
      if (
        thread.source !== "slack" &&
        thread.taskMembership?.role !== "worker" &&
        prevStatus === "running" &&
        TERMINAL_STATUSES.has(status) &&
        !(isViewingThread && thread.id === activeThreadId)
      ) {
        showRunNotification(thread, openThread)
      }
      prev.set(thread.id, status)
    }
  }, [threads, activeThreadId, openThread])
}
