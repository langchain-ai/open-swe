import { useEffect, useState } from "react"

import { formatElapsed } from "@/lib/utils"

/** Counts up from `since`, a server timestamp, or from mount when there is none. */
export function ElapsedSeconds({ since }: { since?: string }) {
  const [mountedAt] = useState(() => Date.now())
  const parsed = since ? Date.parse(since) : Number.NaN
  const startedAt = Number.isFinite(parsed) ? parsed : mountedAt
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])

  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  return (
    <span aria-hidden className="text-xs text-secondary tabular-nums">
      {seconds === 0 ? "0s" : formatElapsed(seconds * 1000)}
    </span>
  )
}
