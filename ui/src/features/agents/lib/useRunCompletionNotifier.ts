import { useEffect, useRef } from "react"

import type { AgentThread } from "@/features/agents/lib/types"
import { showRunNotification } from "@/lib/notifications"

const TERMINAL_STATUSES = new Set(["finished", "error", "interrupted"])

/**
 * Watches the thread list for transitions from `running` to a terminal status
 * and fires a browser notification for each run that completes. Suppresses
 * notifications for the thread the user is currently viewing only when the
 * page is visible — background tabs still notify even for the active thread.
 */
export function useRunCompletionNotifier(
  threads: Array<AgentThread> | undefined,
  activeThreadId?: string
) {
  const prevStatusRef = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    if (!threads) return
    const isViewingThread =
      !!activeThreadId && document.visibilityState === "visible"
    const prev = prevStatusRef.current
    for (const thread of threads) {
      const prevStatus = prev.get(thread.id)
      if (prevStatus === undefined) {
        prev.set(thread.id, thread.status)
        continue
      }
      if (
        prevStatus === "running" &&
        TERMINAL_STATUSES.has(thread.status) &&
        !(isViewingThread && thread.id === activeThreadId)
      ) {
        showRunNotification(thread)
      }
      prev.set(thread.id, thread.status)
    }
  }, [threads, activeThreadId])
}
