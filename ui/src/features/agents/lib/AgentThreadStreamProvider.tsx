import { useCallback, useEffect, useMemo, useRef } from "react"
import {
  STREAM_CONTROLLER,
  StreamProvider,
  useStreamContext,
} from "@langchain/react"
import { Client, overrideFetchImplementation } from "@langchain/langgraph-sdk"
import { useQueryClient } from "@tanstack/react-query"

import { agentsApi } from "./api"
import { agentThreadKeys, invalidateAgentThreadLists } from "./queries"
import type { ReactNode } from "react"

const AGENT_ASSISTANT_ID = "agent"
const LOCAL_AGENT_ASSISTANT_ID = "agent"

const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" })

overrideFetchImplementation(dashboardFetch)

const dashboardRequest = (_url: URL, init: RequestInit): RequestInit => ({
  ...init,
  credentials: "include",
})

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
 * One persistent stream controller for the whole `/agents` subtree. The SDK
 * owns each thread's transport so switching threads or recovering a suspended
 * tab closes the old transport and creates a fresh one.
 */
export function AgentThreadStreamProvider({
  threadId,
  children,
  transport = "cloud",
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
  transport?: "cloud" | "local"
}) {
  const queryClient = useQueryClient()
  const apiUrl =
    transport === "local" ? toAbsoluteApiUrl("/local-graph") : agentStreamApiUrl
  const assistantId =
    transport === "local" ? LOCAL_AGENT_ASSISTANT_ID : AGENT_ASSISTANT_ID
  const client = useMemo(
    () =>
      new Client({
        apiUrl,
        apiKey: null,
        ...(transport === "cloud" ? { onRequest: dashboardRequest } : {}),
      }),
    [apiUrl, transport]
  )

  // The SDK captures the lifecycle callbacks once at controller creation, so
  // they must be stable. Read the live thread id from a ref instead of
  // closing over the (changing) prop.
  const threadIdRef = useRef<string | null>(threadId)
  threadIdRef.current = threadId

  const onCreated = useCallback(() => {
    if (transport === "cloud") invalidateAgentThreadLists(queryClient)
  }, [queryClient, transport])

  const onCompleted = useCallback(() => {
    if (transport !== "cloud") return
    const id = threadIdRef.current
    if (id) {
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.detail(id),
      })
    }
    invalidateAgentThreadLists(queryClient)
  }, [queryClient, transport])

  return (
    <StreamProvider
      apiUrl={apiUrl}
      assistantId={assistantId}
      client={client}
      threadId={threadId ?? undefined}
      onCreated={onCreated}
      onCompleted={onCompleted}
    >
      <ActiveThreadRecovery threadId={threadId} />
      {children}
    </StreamProvider>
  )
}
