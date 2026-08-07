import { useCallback, useEffect, useMemo, useRef } from "react"
import {
  STREAM_CONTROLLER,
  StreamProvider,
  useStreamContext,
} from "@langchain/react"
import {
  ProtocolSseTransportAdapter,
  overrideFetchImplementation,
} from "@langchain/langgraph-sdk"
import { useQueryClient } from "@tanstack/react-query"

import { agentsApi } from "./api"
import { agentThreadKeys, invalidateAgentThreadLists } from "./queries"
import type { ReactNode } from "react"

const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" })

/**
 * Commands, the event stream, and `getState` hydration flow through a
 * {@link ProtocolSseTransportAdapter} backed by {@link dashboardFetch}. But subagent/subgraph
 * discovery on hydrate (`POST /threads/:id/history`) and `getState` itself
 * are issued by the SDK's internal `Client` rather than the transport's
 * `fetch`. Without this, the `Client` falls back to a bare `fetch` that
 * omits the dashboard session cookie cross-origin, so the proxy rejects the
 * read with `401 "not authenticated"`. Override the SDK's global fetch so
 * every `Client` read carries the same credentials as the transport.
 *
 * The transport receives the same fetch through `fetchFactory`, which preserves
 * the SDK's reconnect and idle-heartbeat defaults (passing `fetch` directly
 * disables both). `useAgentThread` still polls run metadata as a fallback for
 * controls such as the stop button.
 */
overrideFetchImplementation(dashboardFetch)

/**
 * The SDK transport builds request URLs as `new URL(apiUrl + path)`, so
 * `apiUrl` must be absolute — a relative base (e.g. "/dashboard/api")
 * makes the SDK fall back to the LangGraph default host
 * (`http://localhost:8123`) and drop the proxy prefix. Promote a
 * same-origin base to an absolute URL using the current origin.
 */
function toAbsoluteApiUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url
  if (typeof window !== "undefined") {
    return `${window.location.origin}${url.startsWith("/") ? "" : "/"}${url}`
  }
  return url
}

const agentStreamApiUrl = toAbsoluteApiUrl(agentsApi.langGraphApiUrl)

function ActiveThreadRecovery({ threadId }: { threadId: string | null }) {
  const stream = useStreamContext()
  const controller = stream[STREAM_CONTROLLER]
  const threadIdRef = useRef(threadId)
  const recoveringRef = useRef(false)
  threadIdRef.current = threadId

  useEffect(() => {
    if (!threadId) return
    const recover = async () => {
      if (
        document.visibilityState !== "visible" ||
        recoveringRef.current ||
        threadIdRef.current !== threadId
      ) {
        return
      }
      recoveringRef.current = true
      try {
        await controller.hydrate(null)
        if (threadIdRef.current === threadId) await controller.hydrate(threadId)
      } finally {
        recoveringRef.current = false
      }
    }
    document.addEventListener("visibilitychange", recover)
    return () => document.removeEventListener("visibilitychange", recover)
  }, [controller, threadId])

  return null
}

/**
 * One persistent stream for the whole `/agents` subtree, mounted by the
 * layout so it survives the home → thread navigation. The SSE transport is
 * reused across thread switches —
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
  threadId: string | null
  children: ReactNode
}) {
  const queryClient = useQueryClient()
  const transport = useMemo(
    () =>
      new ProtocolSseTransportAdapter({
        apiUrl: agentStreamApiUrl,
        fetchFactory: () => dashboardFetch,
      }),
    []
  )

  // The SDK captures the lifecycle callbacks once at controller creation, so
  // they must be stable. Read the live thread id from a ref instead of
  // closing over the (changing) prop.
  const threadIdRef = useRef<string | null>(threadId)
  threadIdRef.current = threadId

  const onCreated = useCallback(() => {
    invalidateAgentThreadLists(queryClient)
  }, [queryClient])

  const onCompleted = useCallback(() => {
    const id = threadIdRef.current
    if (id) {
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.detail(id),
      })
    }
    invalidateAgentThreadLists(queryClient)
  }, [queryClient])

  return (
    <StreamProvider
      transport={transport}
      threadId={threadId ?? undefined}
      onCreated={onCreated}
      onCompleted={onCompleted}
    >
      <ActiveThreadRecovery threadId={threadId} />
      {children}
    </StreamProvider>
  )
}
