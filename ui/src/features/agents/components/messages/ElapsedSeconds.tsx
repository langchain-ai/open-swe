import { useEffect, useState } from "react"

import { formatElapsed } from "@/lib/utils"

function secondsSince(startedAt: number): number {
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
}

/**
 * Seconds since `since` (epoch ms), else since mount. Fixed at mount, so a
 * count carried across a remount keeps going instead of starting over.
 */
export function ElapsedSeconds({ since }: { since?: number }) {
  const [startedAt] = useState(() =>
    since !== undefined && Number.isFinite(since) ? since : Date.now()
  )
  const [seconds, setSeconds] = useState(() => secondsSince(startedAt))

  useEffect(() => {
    const interval = setInterval(() => {
      setSeconds(secondsSince(startedAt))
    }, 1000)
    return () => clearInterval(interval)
  }, [startedAt])

  return (
    <span aria-hidden className="text-xs text-secondary tabular-nums">
      {seconds === 0 ? "0s" : formatElapsed(seconds * 1000)}
    </span>
  )
}
