import { useEffect, useState } from "react"

/** How often a running elapsed timer repaints. */
const TICK_MS = 1000

/**
 * `Date.now()` that repaints while `active`, so live elapsed timers advance.
 * Frozen (and never scheduling a timer) once inactive, so a settled transcript
 * of finished subagents costs nothing.
 */
export function useTickingNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(timer)
  }, [active])

  return now
}
