import { overrideFetchImplementation } from "@langchain/langgraph-sdk";

import { agentsApi } from "./api";

export const AGENT_ASSISTANT_ID = "agent";

export const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" });

/**
 * The custom {@link HttpAgentServerAdapter} only routes `commands`,
 * `stream`, and `state` through {@link dashboardFetch}. History reads
 * (`POST /threads/:id/history`, used for subagent/subgraph discovery on
 * hydrate) are issued by the SDK's internal `Client`, whose
 * `callerOptions`/`fetch` are typed `never` in the custom-adapter branch
 * and therefore can't receive the credentialed fetch. Without this, the
 * `Client` falls back to a bare `fetch` that omits the dashboard session
 * cookie cross-origin, so the proxy rejects history with
 * `401 "not authenticated"`. Override the SDK's global fetch so every
 * `Client` read carries the same credentials as the transport.
 */
overrideFetchImplementation(dashboardFetch);

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
  const encodedThreadId = encodeURIComponent(threadId);
  return {
    commands: `/threads/${encodedThreadId}/commands`,
    stream: `/threads/${encodedThreadId}/stream/events`,
    state: `/threads/${encodedThreadId}/state`,
  };
}
