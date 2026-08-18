import { useEffect, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

/** How long run-idle must persist before subagents are treated as stalled. */
const IDLE_SETTLE_MS = 2000

/**
 * Whether the thread's run is currently executing. `stream.isLoading` flickers
 * between graph steps, so idle has to persist before it counts — otherwise a
 * running subagent would blink to "stalled" on every step boundary.
 *
 * Only callable inside the thread stream provider; callers outside it already
 * know the run is not live.
 */
export function useIsAgentRunActive(): boolean {
  const stream = useAgentThreadStream()
  const isLoading = Boolean(stream.isLoading)
  const [active, setActive] = useState(isLoading)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (isLoading) {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = undefined
      }
      setActive(true)
      return
    }

    timerRef.current = setTimeout(() => {
      timerRef.current = undefined
      setActive(false)
    }, IDLE_SETTLE_MS)

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = undefined
      }
    }
  }, [isLoading])

  return active
}
