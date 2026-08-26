import { useMemo } from "react"
import { StreamProvider } from "@langchain/react"
import { Client, overrideFetchImplementation } from "@langchain/langgraph-sdk"
import { useQuery } from "@tanstack/react-query"

import { agentsApi } from "./api"
import { agentThreadKeys } from "./queries"
import { transportForThread } from "./threadTransport"
import type { ReactNode } from "react"

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

/**
 * One persistent stream controller for the whole `/agents` subtree. The SDK
 * owns each thread's transport and reconnect lifecycle.
 */
export function AgentThreadStreamProvider({
  threadId,
  children,
  transport: transportOverride,
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
  const threadQuery = useQuery({
    queryKey: agentThreadKeys.detail(threadId ?? ""),
    queryFn: () => agentsApi.getThread(threadId as string),
    enabled: Boolean(threadId),
    staleTime: 30_000,
  })
  const environment =
    transportOverride ?? threadQuery.data?.environment ?? "cloud"
  const transport = transportForThread(
    threadQuery.data ? { ...threadQuery.data, environment } : undefined
  )
  const base = transport.streamBase(threadQuery.data)
  const apiUrl =
    environment === "cloud" ? agentStreamApiUrl : toAbsoluteApiUrl(base.apiUrl)
  const assistantId = base.assistantId
  const client = useMemo(
    () =>
      new Client({
        apiUrl,
        apiKey: null,
        ...(environment === "cloud" ? { onRequest: dashboardRequest } : {}),
      }),
    [apiUrl, environment]
  )

  return (
    <StreamProvider
      apiUrl={apiUrl}
      assistantId={assistantId}
      client={client}
      threadId={threadId ?? undefined}
    >
      {children}
    </StreamProvider>
  )
}
