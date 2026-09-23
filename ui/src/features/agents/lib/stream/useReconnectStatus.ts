import { useEffect, useState } from "react"

import { reconnectLabel } from "./reconnectLabel"
import type { StreamConnection } from "./connection"

export interface ReconnectStatus {
  /** Activity-line text while the stream is down; `null` once it is serving. */
  label: string | null
}

/** Drives the reconnect countdown, ticking only while one is pending. */
export function useConnectionStatus(
  connection: StreamConnection
): ReconnectStatus {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (connection.status !== "reconnecting") return
    // A countdown has to read the wall clock; the alternative is reading it
    // during render, which the purity rule rejects for the same reason.
    // oxlint-disable-next-line react/set-state-in-effect
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [connection])

  return { label: reconnectLabel(connection, now) }
}
