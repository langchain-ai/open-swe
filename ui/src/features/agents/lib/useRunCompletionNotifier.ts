import { useEffect, useRef } from "react"

import type { AgentThread } from "@/features/agents/lib/types"
import { showRunNotification } from "@/lib/notifications"

const TERMINAL_STATUSES = new Set(["finished", "error", "interrupted"])

/**
 * Watches the thread list for transitions from `running` to a terminal status
 * and fires a browser notification saying what each completed run did. Task workers
 * stay quiet because their coordinator handles user communication. Suppresses
 * notifications for the thread the user is currently viewing only when the
 * page is visible — background tabs still notify even for the active thread.
 */
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
