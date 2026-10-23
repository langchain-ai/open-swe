import { useEffect, useRef } from "react"

import { useStreamPool } from "./streamPool"

const RECONNECT_NOTICE_DELAY_MS = 3_000

interface ReconnectAttempt {
  attempt: number
  delayMs: number
}

export function useReconnectNotice(streamId: string) {
  const timer = useRef<ReturnType<typeof setTimeout>>(null)
  const pending = useRef<ReconnectAttempt>(null)

  const clear = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    pending.current = null
    // A recovery chain owns the connection while it holds the turn; the SDK
    // reconnecting or stopping underneath it must not erase that state.
    const entry = useStreamPool
      .getState()
      .entries.find((candidate) => candidate.id === streamId)
    const connection = entry?.connection
    if (
      connection?.status === "failed" ||
      (connection?.status === "reconnecting" && connection.source === "resume")
    )
      return
    useStreamPool.getState().streamLive(streamId)
  }

  const schedule = (reconnect: ReconnectAttempt) => {
    pending.current = reconnect
    if (timer.current) return
    timer.current = setTimeout(() => {
      timer.current = null
      if (!pending.current) return
      useStreamPool
        .getState()
        .streamReconnecting(
          streamId,
          pending.current.attempt,
          Date.now() + pending.current.delayMs
        )
    }, RECONNECT_NOTICE_DELAY_MS)
  }

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
      useStreamPool.getState().streamLive(streamId)
    },
    [streamId]
  )

  return { schedule, clear }
}
