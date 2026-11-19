/**
 * Request-level timing for the handful of dashboard calls that gate a thread:
 * time to response headers (for a state read, then its body and its parse) plus
 * the backend's `Server-Timing` phase breakdown. Only timings and header
 * metadata are recorded, never bodies, cookies, or tokens.
 */

import { startSpan } from "./trace"

export interface ServerTimingEntry {
  name: string
  /** Milliseconds, when the backend reported one. */
  duration: number | null
}

export type TimedRequestKind =
  | "thread_detail"
  | "thread_state"
  /** The transcript log's snapshot, which hydrates a thread in place of `thread_state`. */
  | "thread_transcript"
  | "stream_events"
  | "command"
  /** The SDK's lookups for a run to join or queue when it opens a thread. */
  | "running_lookup"
  | "pending_lookup"

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
  /** A state read reports again once parsed: its body's download after the headers. */
  bodyMs?: number
  parseMs?: number
}

type RequestTimingListener = (timing: RequestTiming) => void

const listeners = new Set<RequestTimingListener>()

const THREAD_REQUEST_RE =
  /\/dashboard\/api\/threads\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(\/[^?#]*)?$/i

const KINDS_BY_SUFFIX: Record<string, TimedRequestKind> = {
  "": "thread_detail",
  "/state": "thread_state",
  "/transcript": "thread_transcript",
  "/stream/events": "stream_events",
  "/commands": "command",
  // `@langchain/react` 2.0 starts a run and streams it in one request.
  "/runs/stream": "command",
}

const LOOKUPS_BY_STATUS: Record<string, TimedRequestKind> = {
  running: "running_lookup",
  pending: "pending_lookup",
}

export function classifyDashboardRequest(
  url: string
): ClassifiedRequest | null {
  const pathname = url.split(/[?#]/, 1)[0] ?? ""
  const match = THREAD_REQUEST_RE.exec(pathname)
  if (!match?.[1]) return null
  const suffix = (match[2] ?? "").replace(/\/$/, "")
  const kind =
    suffix === "/runs"
      ? LOOKUPS_BY_STATUS[
          new URL(url, "http://localhost").searchParams.get("status") ?? ""
        ]
      : KINDS_BY_SUFFIX[suffix]
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
export function withRequestTiming(
  fetchImpl: typeof fetch,
  { measureLists = false }: { measureLists?: boolean } = {}
): typeof fetch {
  return async (input, init) => {
    if (typeof window === "undefined") return fetchImpl(input, init)
    const url = requestUrl(input)
    const classified = classifyDashboardRequest(url)
    const method = String(
      init?.method ??
        (typeof input === "string" || input instanceof URL
          ? "GET"
          : input.method)
    ).toUpperCase()
    const listKind =
      measureLists && method === "GET" && url
        ? /\/dashboard\/api\/threads\/(pinned|repos|page)\/?$/i
            .exec(url.split(/[?#]/, 1)[0] ?? "")?.[1]
            ?.toLowerCase()
        : undefined
    if (!classified && !listKind) return fetchImpl(input, init)
    const started = performance.now()
    const request = fetchImpl(input, init) as Promise<Response> & {
      __openSweWarmTiming?: { startedAt?: number; endedAt?: number }
    }
    const warmTiming = listKind ? request.__openSweWarmTiming : undefined
    let listSpan: ReturnType<typeof startSpan> | undefined
    try {
      if (listKind)
        listSpan = startSpan(
          "thread_list_request",
          { endpoint: listKind },
          { startedAt: warmTiming?.startedAt ?? started }
        )
    } catch {}
    let response: Response
    try {
      response = await request
    } catch (error) {
      try {
        listSpan?.end({ outcome: "error" }, { endedAt: warmTiming?.endedAt })
      } catch {}
      throw error
    }
    try {
      listSpan?.end(
        { status: response.status, outcome: response.ok ? "success" : "error" },
        { endedAt: warmTiming?.endedAt }
      )
    } catch {}
    if (classified) {
      const timing: RequestTiming = {
        ...classified,
        status: response.status,
        ttfbMs: performance.now() - started,
        serverTiming: parseServerTiming(response.headers.get("server-timing")),
      }
      recordRequestTiming(timing)
      // `json()` would time the download and the parse as one.
      if (classified.kind === "thread_state")
        response.json = async () => {
          const text = await response.text()
          const bodyAt = performance.now()
          const parsed: unknown = JSON.parse(text)
          recordRequestTiming({
            ...timing,
            bodyMs: bodyAt - started - timing.ttfbMs,
            parseMs: performance.now() - bodyAt,
          })
          return parsed
        }
    }
    return response
  }
}
