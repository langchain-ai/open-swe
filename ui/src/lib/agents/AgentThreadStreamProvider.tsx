import { useCallback, useRef } from "react";
import { StreamProvider } from "@langchain/react";
import { overrideFetchImplementation } from "@langchain/langgraph-sdk";
import { useQueryClient } from "@tanstack/react-query";

import { agentsApi } from "./api";
import { agentThreadKeys } from "./queries";
import type { ReactNode } from "react";

const AGENT_ASSISTANT_ID = "agent";

const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" });

/**
 * We use the SDK's built-in `sse` transport (via {@link StreamProvider}'s
 * `apiUrl` + `fetch`), so commands, the event stream, and `getState`
 * hydration all flow through {@link dashboardFetch}. But subagent/subgraph
 * discovery on hydrate (`POST /threads/:id/history`) and `getState` itself
 * are issued by the SDK's internal `Client` rather than the transport's
 * `fetch`. Without this, the `Client` falls back to a bare `fetch` that
 * omits the dashboard session cookie cross-origin, so the proxy rejects the
 * read with `401 "not authenticated"`. Override the SDK's global fetch so
 * every `Client` read carries the same credentials as the transport.
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

const agentStreamApiUrl = toAbsoluteApiUrl(agentsApi.langGraphApiUrl);

/**
 * One persistent stream for the whole `/agents` subtree, mounted by the
 * layout so it survives the home → thread navigation. The built-in `sse`
 * transport (default `apiUrl` branch) is reused across thread switches —
 * changing `threadId` re-hydrates the same controller instead of tearing
 * down a per-thread transport — which is what lets a home-page
 * `stream.submit` keep streaming after we navigate to the minted thread.
 */
export function AgentThreadStreamProvider({
  threadId,
  children,
}: {
  /**
   * The active thread, or `null` on routes without one (the Agents home,
   * automations). A `null` id leaves the SDK in its lazy-create mode: the
   * first `stream.submit` mints the thread id, fires `onThreadId`, and skips
   * the `getState` hydrate — so a fresh thread needs no client-minted id and
   * no `getState` 404 round-trip.
   */
  threadId: string | null;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();

  // The SDK captures the lifecycle callbacks once at controller creation, so
  // they must be stable. Read the live thread id from a ref instead of
  // closing over the (changing) prop.
  const threadIdRef = useRef<string | null>(threadId);
  threadIdRef.current = threadId;

  const onCreated = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true });
  }, [queryClient]);

  const onCompleted = useCallback(() => {
    const id = threadIdRef.current;
    if (id) {
      void queryClient.invalidateQueries({ queryKey: agentThreadKeys.detail(id) });
    }
    void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true });
  }, [queryClient]);

  return (
    <StreamProvider
      apiUrl={agentStreamApiUrl}
      assistantId={AGENT_ASSISTANT_ID}
      fetch={dashboardFetch}
      threadId={threadId ?? undefined}
      onCreated={onCreated}
      onCompleted={onCompleted}
    >
      {children}
    </StreamProvider>
  );
}
