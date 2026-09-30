import { useCallback, useEffect, useRef, useState } from "react"

import { LIVE_CONNECTION } from "./connection"
import type { StreamConnection } from "./connection"

const RECONNECT_NOTICE_DELAY_MS = 3_000

interface ReconnectAttempt {
  attempt: number
  delayMs: number
}

export interface ReconnectNotice {
  connection: StreamConnection
  /** Hand to `useStream`'s `onReconnect`. */
  onReconnect: (reconnect: ReconnectAttempt) => void
  /** Hand to `useStream`'s `onConnected`, and call when the run ends. */
  onConnected: () => void
}

/**
 * The owning stream's liveness, with a grace period: a retry that resolves
 * within a few seconds never reaches the UI, so a routine blip does not flash
 * a reconnect notice.
 */
export function useReconnectNotice(): ReconnectNotice {
  const [connection, setConnection] =
    useState<StreamConnection>(LIVE_CONNECTION)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<ReconnectAttempt | null>(null)

  const onConnected = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    pending.current = null
    setConnection(LIVE_CONNECTION)
  }, [])

  const onReconnect = useCallback((reconnect: ReconnectAttempt) => {
    pending.current = reconnect
    if (timer.current) return
    timer.current = setTimeout(() => {
      timer.current = null
      const next = pending.current
      if (!next) return
      setConnection({
        status: "reconnecting",
        attempt: next.attempt,
        retryAt: Date.now() + next.delayMs,
      })
    }, RECONNECT_NOTICE_DELAY_MS)
  }, [])

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    []
  )

  return { connection, onReconnect, onConnected }
}
