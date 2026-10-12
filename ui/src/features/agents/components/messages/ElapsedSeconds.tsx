import { useEffect, useState } from "react"

function startTime(since?: string): number {
  const parsed = since ? Date.parse(since) : Number.NaN
  return Number.isNaN(parsed) ? Date.now() : Math.min(parsed, Date.now())
}

function secondsSince(startedAt: number): number {
  return Math.floor((Date.now() - startedAt) / 1000)
}

/** Counts up whole seconds from `since` (an ISO timestamp), or from mount. */
export function ElapsedSeconds({ since }: { since?: string }) {
  const [startedAt] = useState(() => startTime(since))
  const [seconds, setSeconds] = useState(() => secondsSince(startedAt))

  useEffect(() => {
    const interval = setInterval(() => {
      setSeconds(secondsSince(startedAt))
    }, 1000)
    return () => clearInterval(interval)
  }, [startedAt])

  return (
    <span aria-hidden className="text-xs text-secondary tabular-nums">
      {seconds}s
    </span>
  )
}
