import { agentsApi } from "./api";

export const AGENT_ASSISTANT_ID = "agent";

export const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" });

/**
 * The SDK transport builds request URLs as `new URL(apiUrl + path)`, so
 * `apiUrl` must be absolute — a relative base (e.g. "/dashboard/api")
 * makes the SDK fall back to the LangGraph default host
 * (`http://localhost:8123`) and drop the proxy prefix. Promote a
 * same-origin base to an absolute URL using the current origin.
 */
function toAbsoluteApiUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url;
  if (typeof window !== "undefined") {
    return `${window.location.origin}${url.startsWith("/") ? "" : "/"}${url}`;
  }
  return url;
}

export const agentStreamApiUrl = toAbsoluteApiUrl(agentsApi.langGraphApiUrl);

/**
 * Explicit v2-protocol paths bound to the dashboard's auth proxy
 * (`agent/dashboard/routes.py`). Using a custom transport with these
 * paths keeps commands, the event stream, and `getState` hydration all
 * flowing through the same credentialed fetch — the built-in `apiUrl`
 * branch hydrates state via an internal client that doesn't carry the
 * dashboard session cookie.
 */
export function agentStreamPaths(threadId: string) {
  return {
    commands: `/threads/${threadId}/commands`,
    stream: `/threads/${threadId}/stream/events`,
    state: `/threads/${threadId}/state`,
  };
}
