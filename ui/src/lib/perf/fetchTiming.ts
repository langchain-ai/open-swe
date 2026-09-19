/**
 * Request-level timing for the handful of dashboard calls that gate a thread:
 * time to response headers plus the backend's `Server-Timing` phase breakdown.
 * Only header metadata is recorded; never bodies, cookies, or tokens.
 */

export interface ServerTimingEntry {
  name: string
  /** Milliseconds, when the backend reported one. */
  duration: number | null
}

export type TimedRequestKind =
  | "thread_detail"
  | "thread_state"
  | "stream_events"
  | "command"

export interface ClassifiedRequest {
  kind: TimedRequestKind
  /** Lower-cased thread id from the path, so timings reach the right stream. */
  threadId: string
}

export interface RequestTiming extends ClassifiedRequest {
  status: number
  /** Milliseconds from request start to response headers. */
  ttfbMs: number
  serverTiming: Array<ServerTimingEntry>
}

type RequestTimingListener = (timing: RequestTiming) => void

const listeners = new Set<RequestTimingListener>()

const THREAD_REQUEST_RE =
  /\/dashboard\/api\/threads\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(\/[^?#]*)?$/i

const KINDS_BY_SUFFIX: Record<string, TimedRequestKind> = {
  "": "thread_detail",
  "/state": "thread_state",
  "/stream/events": "stream_events",
  "/commands": "command",
}

export function classifyDashboardRequest(
  url: string
): ClassifiedRequest | null {
  const pathname = url.split(/[?#]/, 1)[0] ?? ""
  const match = THREAD_REQUEST_RE.exec(pathname)
  if (!match?.[1]) return null
  const suffix = (match[2] ?? "").replace(/\/$/, "")
  const kind = KINDS_BY_SUFFIX[suffix]
  return kind ? { kind, threadId: match[1].toLowerCase() } : null
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
    const dur = params
      .map((param) => param.trim())
      .find((param) => /^dur=/i.test(param))
    const parsed = dur ? Number(dur.slice(4)) : NaN
    entries.push({
      name: name.trim(),
      duration: Number.isFinite(parsed) ? parsed : null,
    })
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
    const classified =
      typeof window === "undefined"
        ? null
        : classifyDashboardRequest(requestUrl(input))
    if (!classified) return fetchImpl(input, init)
    const started = performance.now()
    const response = await fetchImpl(input, init)
    recordRequestTiming({
      ...classified,
      status: response.status,
      ttfbMs: performance.now() - started,
      serverTiming: parseServerTiming(response.headers.get("server-timing")),
    })
    return response
  }
}
