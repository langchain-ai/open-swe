import { Client } from "@langchain/langgraph-sdk"

import { withRequestTiming } from "@/lib/perf/fetchTiming"

/** Streams and client reads must carry the session cookie across origins. */
export const dashboardFetch: typeof fetch = withRequestTiming((input, init) =>
  fetch(input, { ...init, credentials: "include" })
)

/** The SDK builds request URLs with `new URL(apiUrl + path)`, so the base must be absolute. */
export function absoluteApiUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url
  if (typeof window !== "undefined") {
    return `${window.location.origin}${url.startsWith("/") ? "" : "/"}${url}`
  }
  return url
}

export type ThreadStateView = "full" | "trimmed"

const THREAD_STATE_PATH_RE = /\/threads\/[^/?#]+\/state$/

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input
  if (input instanceof URL) return input.toString()
  return input.url
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  const method =
    init?.method ?? (input instanceof Request ? input.method : "GET")
  return method.toUpperCase()
}

/**
 * Route the SDK's hydration read (`GET …/threads/:id/state`) to a server-side
 * view. The trimmed view blanks large tool outputs and pasted images so the
 * transcript can paint from a few hundred kilobytes; the blanks are filled in
 * on demand from the message cache.
 */
export function withThreadStateView(
  fetchImpl: typeof fetch,
  view: ThreadStateView
): typeof fetch {
  if (view === "full") return fetchImpl
  return (input, init) => {
    const url = requestUrl(input)
    const [pathname, query] = url.split(/[?#]/, 2)
    if (
      requestMethod(input, init) !== "GET" ||
      query !== undefined ||
      !THREAD_STATE_PATH_RE.test(pathname ?? "")
    ) {
      return fetchImpl(input, init)
    }
    const rewritten = `${url}?view=${view}`
    return fetchImpl(
      input instanceof Request ? new Request(rewritten, input) : rewritten,
      init
    )
  }
}

/** A LangGraph client for a dashboard-proxied graph endpoint. */
export function createDashboardClient(
  apiUrl: string,
  options: { stateView?: ThreadStateView } = {}
): Client {
  return new Client({
    apiUrl: absoluteApiUrl(apiUrl),
    apiKey: null,
    callerOptions: {
      fetch: withThreadStateView(dashboardFetch, options.stateView ?? "full"),
    },
  })
}

/** A LangGraph client for the desktop app's local graph proxy. */
export function createLocalGraphClient(): Client {
  return new Client({ apiUrl: absoluteApiUrl("/local-graph"), apiKey: null })
}
