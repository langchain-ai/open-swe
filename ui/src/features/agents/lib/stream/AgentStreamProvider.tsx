import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react"
import { useChannelEffect, useStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"

import { agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
} from "@/features/agents/lib/queries"
import {
  createDashboardClient,
  createLocalGraphClient,
  dashboardFetch,
} from "@/lib/langgraph-client"
import {
  MAX_RECONNECT_ATTEMPTS,
  RECONNECT_GIVE_UP_GRACE_MS,
  reconnectDelayMs,
  selectConnectionFor,
  selectStreamFor,
  useStreamPool,
} from "./streamPool"
import type { ReactNode } from "react"
import type {
  AgentStream,
  AgentThreadTransport,
  StreamConnection,
  StreamPoolEntry,
} from "./streamPool"

export type {
  AgentStream,
  AgentThreadTransport,
  StreamConnection,
} from "./streamPool"

const AGENT_ASSISTANT_ID = "agent"
const SWEEP_INTERVAL_MS = 10_000

/**
 * Reports every event-stream open against an instance so the transport's
 * otherwise-silent reconnect loop has an observable "we are serving again"
 * edge; the SDK fires `onReconnect` before each attempt but nothing on success.
 */
function connectionSensingFetch(id: string): typeof fetch {
  return async (input, init) => {
    const url = input instanceof Request ? input.url : String(input)
    if (!url.includes("/stream/events")) return dashboardFetch(input, init)
    const response = await dashboardFetch(input, init)
    if (response.ok) useStreamPool.getState().streamLive(id)
    return response
  }
}

const AgentStreamContext = createContext<AgentStream | null>(null)

export function useAgentStream(): AgentStream {
  const stream = useContext(AgentStreamContext)
  if (!stream) throw new Error("useAgentStream requires AgentStreamProvider")
  return stream
}

/** One SDK stream, kept mounted for as long as the pool retains its entry. */
function PooledStream({ entry }: { entry: StreamPoolEntry }) {
  const queryClient = useQueryClient()
  const cloud = entry.transport === "cloud"
  const client = useMemo(
    () =>
      cloud
        ? createDashboardClient(agentsApi.langGraphApiUrl)
        : createLocalGraphClient(),
    [cloud]
  )
  const pool = useStreamPool.getState
  const [isOffloading, setIsOffloading] = useState(false)
  const sensingFetch = useMemo(
    () => connectionSensingFetch(entry.id),
    [entry.id]
  )

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId: entry.threadId,
    fetch: sensingFetch,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    reconnectDelayMs,
    onReconnect: ({ attempt }) =>
      pool().streamReconnecting(entry.id, attempt, Date.now()),
    onThreadId: (threadId) => pool().rekey(entry.id, threadId),
    onCreated: () => {
      setIsOffloading(false)
      pool().runAccepted(entry.id)
      if (cloud) invalidateAgentThreadLists(queryClient)
    },
    onCompleted: () => {
      setIsOffloading(false)
      if (!cloud) return
      const threadId = pool().entries.find((e) => e.id === entry.id)?.threadId
      if (threadId) {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.detail(threadId),
        })
      }
      invalidateAgentThreadLists(queryClient)
    },
  })

  useChannelEffect(stream, ["custom"], {
    onEvent: (event) => {
      if (event.method !== "custom" || event.params.namespace.length) return
      const payload = event.params.data.payload
      if (payload?.type === "conversation_offloading") {
        setIsOffloading(payload.status === "started")
      }
    },
    onError: () => setIsOffloading(false),
  })

  const publish = useStreamPool((state) => state.publish)
  useLayoutEffect(
    () => publish(entry.id, { ...stream, isOffloading }),
    [entry.id, publish, stream, isOffloading]
  )

  const { connection } = entry
  useEffect(() => {
    if (connection.status !== "reconnecting") return
    if (connection.attempt < MAX_RECONNECT_ATTEMPTS) return
    const timer = setTimeout(
      () => pool().streamLost(entry.id),
      Math.max(0, connection.retryAt - Date.now()) + RECONNECT_GIVE_UP_GRACE_MS
    )
    return () => clearTimeout(timer)
  }, [connection, entry.id, pool])

  return null
}

/**
 * Owns every live `useStream` under `/agents`. The bound thread is the one the
 * route asks for; threads left within the last minute (and any still running)
 * stay mounted so returning to them is instant and never orphans a run.
 */
export function AgentStreamProvider({
  threadId,
  transport = "cloud",
  onThreadCreated,
  children,
}: {
  threadId: string | null
  transport?: AgentThreadTransport
  /** Fires once the server has accepted the first run of a lazily created thread. */
  onThreadCreated?: (threadId: string) => void
  children: ReactNode
}) {
  const activate = useStreamPool((state) => state.activate)
  const sweep = useStreamPool((state) => state.sweep)
  const consumeCreatedThread = useStreamPool(
    (state) => state.consumeCreatedThread
  )
  const entries = useStreamPool((state) => state.entries)
  const createdThreadId = useStreamPool((state) => state.createdThreadId)
  const stream = useStreamPool((state) =>
    selectStreamFor(state, transport, threadId)
  )

  useLayoutEffect(
    () => activate(transport, threadId),
    [activate, threadId, transport]
  )

  useEffect(() => {
    const timer = setInterval(() => sweep(Date.now()), SWEEP_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [sweep])

  useEffect(() => {
    if (!createdThreadId) return
    consumeCreatedThread()
    onThreadCreated?.(createdThreadId)
  }, [consumeCreatedThread, createdThreadId, onThreadCreated])

  return (
    <>
      {entries.map((entry) => (
        // langgraphjs#2817: a dead pump can never be reopened on its own
        // controller, so recovery is a fresh instance under a fresh key.
        <PooledStream key={`${entry.id}:${entry.epoch}`} entry={entry} />
      ))}
      {stream && (
        <AgentStreamContext.Provider value={stream}>
          {children}
        </AgentStreamContext.Provider>
      )}
    </>
  )
}

/** Liveness of the bound thread's event stream, with its manual retry. */
export function useAgentStreamConnection(
  transport: AgentThreadTransport,
  threadId: string | null
): { connection: StreamConnection; retry: () => void } {
  const connection = useStreamPool((state) =>
    selectConnectionFor(state, transport, threadId)
  )
  const retryStream = useStreamPool((state) => state.retryStream)
  const retry = useCallback(
    () => retryStream(transport, threadId),
    [retryStream, threadId, transport]
  )
  return { connection, retry }
}
