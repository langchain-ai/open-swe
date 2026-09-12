import { MAX_RECONNECT_ATTEMPTS, type StreamConnection } from "./streamPool"

/**
 * The activity line's text while the event stream is not serving. Mirrors how
 * Claude Code and Codex report a retry: attempt count plus a countdown, in the
 * same slot the normal status occupies rather than an alert of its own.
 */
export function reconnectLabel(
  connection: StreamConnection,
  now: number
): string | null {
  if (connection.status === "live") return null
  if (connection.status === "lost") return "Connection lost"
  const seconds = Math.max(0, Math.ceil((connection.retryAt - now) / 1000))
  const progress = `${connection.attempt}/${MAX_RECONNECT_ATTEMPTS}`
  return seconds > 0
    ? `Reconnecting… ${progress} (retrying in ${seconds}s)`
    : `Reconnecting… ${progress}`
}
