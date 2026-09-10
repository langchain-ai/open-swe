import { useEffect, useRef } from "react"

import { trackDatadogAction } from "@/lib/datadog"
import { useAgentStream } from "./AgentStreamProvider"
import { useStreamPool } from "./streamPool"

/** How long server truth and the stream may disagree before the instance is remounted. */
export const RECONCILE_GRACE_MS = 5_000

/**
 * Remount the pooled `useStream` instance for a cloud thread when it stops
 * agreeing with the polled thread status. A retained instance is never
 * re-hydrated on return, so two states are otherwise permanent: a run started
 * elsewhere that an idle instance never subscribed to (server running, stream
 * idle), and a dead event stream that froze `isLoading` on (server idle,
 * stream loading). One kick per disagreement; a fresh instance that is still
 * hydrating is left alone.
 */
export function useReconcileStream(threadId: string, running: boolean) {
  const stream = useAgentStream()
  const kick = useStreamPool((state) => state.kick)
  const kicked = useRef(false)
  const mismatch =
    stream.threadId === threadId &&
    !stream.isThreadLoading &&
    running !== stream.isLoading

  useEffect(() => {
    if (!mismatch) {
      kicked.current = false
      return
    }
    if (kicked.current) return
    const timer = setTimeout(() => {
      kicked.current = true
      trackDatadogAction("agent-stream.kick", {
        threadId,
        serverRunning: running,
      })
      kick("cloud", threadId)
    }, RECONCILE_GRACE_MS)
    return () => clearTimeout(timer)
  }, [kick, mismatch, running, threadId])
}
