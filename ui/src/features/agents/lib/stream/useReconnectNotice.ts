import { useCallback, useEffect, useRef } from "react"

import { useStreamConnection } from "./streamConnection"

const RECONNECT_NOTICE_DELAY_MS = 3_000

interface ReconnectAttempt {
  attempt: number
  delayMs: number
}

/**
 * Debounces reconnect notices: a retry only surfaces as "reconnecting" once
 * it has lasted a few seconds, so brief interruptions never flash the label.
 */
export function useReconnectNotice() {
  const timer = useRef<ReturnType<typeof setTimeout>>(null)
  const pending = useRef<ReconnectAttempt>(null)

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    pending.current = null
    useStreamConnection.getState().live()
  }, [])

  const schedule = useCallback((reconnect: ReconnectAttempt) => {
    pending.current = reconnect
    if (timer.current) return
    timer.current = setTimeout(() => {
      timer.current = null
      if (!pending.current) return
      useStreamConnection
        .getState()
        .reconnecting(
          pending.current.attempt,
          Date.now() + pending.current.delayMs
        )
    }, RECONNECT_NOTICE_DELAY_MS)
  }, [])

  useEffect(() => clear, [clear])

  return { schedule, clear }
}
