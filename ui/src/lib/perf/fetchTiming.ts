/**
 * Request-level timing for the handful of dashboard calls that gate a thread:
 * time to response headers plus the backend's `Server-Timing` phase breakdown.
 * Only header metadata is recorded; never bodies, cookies, or tokens.
 */

export interface ServerTimingEntry {
  name: string
  /** Milliseconds, when the backend reported one. */
  duration: number | null
  description: string | null
}

export type TimedRequestKind =
  | "thread_detail"
  | "thread_state"
  | "stream_events"
  | "command"

export interface RequestTiming {
  kind: TimedRequestKind
  status: number
  /** Milliseconds from request start to response headers. */
  ttfbMs: number
  serverTiming: Array<ServerTimingEntry>
}

type RequestTimingListener = (timing: RequestTiming) => void

const listeners = new Set<RequestTimingListener>()

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
const THREAD_DETAIL_RE = new RegExp(`/dashboard/api/threads/${UUID}/?$`, "i")
const THREAD_STATE_RE = new RegExp(
  `/dashboard/api/threads/${UUID}/state/?$`,
  "i"
)
const STREAM_EVENTS_RE = new RegExp(
  `/dashboard/api/threads/${UUID}/stream/events/?$`,
  "i"
)
const COMMAND_RE = new RegExp(`/dashboard/api/threads/${UUID}/commands/?$`, "i")

export function classifyDashboardRequest(url: string): TimedRequestKind | null {
  const pathname = url.split(/[?#]/, 1)[0] ?? ""
  if (THREAD_STATE_RE.test(pathname)) return "thread_state"
  if (STREAM_EVENTS_RE.test(pathname)) return "stream_events"
  if (COMMAND_RE.test(pathname)) return "command"
  if (THREAD_DETAIL_RE.test(pathname)) return "thread_detail"
  return null
}

/** Parse a `Server-Timing` header (RFC 9209 style `name;dur=1.2;desc=x, ...`). */
export function parseServerTiming(
  header: string | null
): Array<ServerTimingEntry> {
  if (!header) return []
  const entries: Array<ServerTimingEntry> = []
  for (const raw of header.split(",")) {
    const [name, ...params] = raw.trim().split(";")
    if (!name) continue
    let duration: number | null = null
    let description: string | null = null
    for (const param of params) {
      const [key, ...rest] = param.trim().split("=")
      const value = rest.join("=").replace(/^"|"$/g, "")
      if (key?.toLowerCase() === "dur") {
        const parsed = Number(value)
        duration = Number.isFinite(parsed) ? parsed : null
      } else if (key?.toLowerCase() === "desc") {
        description = value
      }
    }
    entries.push({ name: name.trim(), duration, description })
  }
  return entries
}

export function subscribeRequestTimings(
  listener: RequestTimingListener
): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function recordRequestTiming(timing: RequestTiming): void {
  for (const listener of listeners) {
    try {
      listener(timing)
    } catch {}
  }
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input
  if (input instanceof URL) return input.href
  return input.url
}

/**
 * Wrap a `fetch` so the dashboard's thread requests report their timing. The
 * wrapped function looks `fetch` up at call time, which keeps the head warmup
 * script's hand-off patch (`apiWarmup.ts`) working underneath it.
 */
export function withRequestTiming(fetchImpl: typeof fetch): typeof fetch {
  return async (input, init) => {
    const kind =
      typeof window === "undefined"
        ? null
        : classifyDashboardRequest(requestUrl(input))
    if (!kind) return fetchImpl(input, init)
    const started = performance.now()
    const response = await fetchImpl(input, init)
    recordRequestTiming({
      kind,
      status: response.status,
      ttfbMs: performance.now() - started,
      serverTiming: parseServerTiming(response.headers.get("server-timing")),
    })
    return response
  }
}
