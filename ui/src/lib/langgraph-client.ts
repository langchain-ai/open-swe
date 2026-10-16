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

/** A LangGraph client for a dashboard-proxied graph endpoint. */
export function createDashboardClient(
  apiUrl: string,
  fetcher: typeof fetch = dashboardFetch
): Client {
  return new Client({
    apiUrl: absoluteApiUrl(apiUrl),
    apiKey: null,
    callerOptions: { fetch: fetcher },
  })
}
