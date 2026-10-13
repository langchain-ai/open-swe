import { useEffect, useState } from "react"

import { formatElapsed } from "@/lib/utils"

export function ElapsedSeconds() {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const startedAt = Date.now()
    const interval = setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  return (
    <span aria-hidden className="text-xs text-secondary tabular-nums">
      {seconds === 0 ? "0s" : formatElapsed(seconds * 1000)}
    </span>
  )
}
