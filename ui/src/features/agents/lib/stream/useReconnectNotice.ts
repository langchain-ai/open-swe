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
