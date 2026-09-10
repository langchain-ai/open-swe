import { Client } from "@langchain/langgraph-sdk"

/** Streams and client reads must carry the session cookie across origins. */
export const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" })

const withCredentials = (_url: URL, init: RequestInit): RequestInit => ({
  ...init,
  credentials: "include",
})

/** The SDK builds request URLs with `new URL(apiUrl + path)`, so the base must be absolute. */
export function absoluteApiUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url
  if (typeof window !== "undefined") {
    return `${window.location.origin}${url.startsWith("/") ? "" : "/"}${url}`
  }
  return url
}

/** A LangGraph client for a dashboard-proxied graph endpoint. */
export function createDashboardClient(apiUrl: string): Client {
  return new Client({
    apiUrl: absoluteApiUrl(apiUrl),
    apiKey: null,
    onRequest: withCredentials,
  })
}

/** A LangGraph client for the desktop app's local graph proxy. */
export function createLocalGraphClient(): Client {
  return new Client({ apiUrl: absoluteApiUrl("/local-graph"), apiKey: null })
}
