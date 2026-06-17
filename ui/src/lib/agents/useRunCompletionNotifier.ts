import { useEffect, useRef } from "react"

import type { AgentThread } from "@/lib/agents/types"
import { showRunNotification } from "@/lib/notifications"

const TERMINAL_STATUSES = new Set(["finished", "error", "interrupted"])

/**
 * Watches the thread list for transitions from `running` to a terminal status
 * and fires a browser notification for each run that completes. Skips the
 * thread the user is currently viewing (that tab is already in focus).
 */
export function useRunCompletionNotifier(
  threads: Array<AgentThread> | undefined,
  activeThreadId?: string
) {
  const prevStatusRef = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    if (!threads) return
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
        thread.id !== activeThreadId
      ) {
        showRunNotification(thread)
      }
      prev.set(thread.id, thread.status)
    }
  }, [threads, activeThreadId])
}
