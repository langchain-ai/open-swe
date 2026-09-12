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
import { RunTracker } from "@/lib/perf/streaming"
import {
  MAX_RECONNECT_ATTEMPTS,
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
  const [runTracker] = useState(
    () =>
      new RunTracker({ transport: entry.transport, threadId: entry.threadId })
  )
  useEffect(() => () => runTracker.dispose(), [runTracker])
  const [isOffloading, setIsOffloading] = useState(false)
  const [routed, setRouted] = useState<{
    route?: string
    modelId?: string | null
  } | null>(null)

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId: entry.threadId,
    fetch: dashboardFetch,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    reconnectDelayMs,
    onReconnect: ({ attempt, delayMs }) =>
      pool().streamReconnecting(entry.id, attempt, Date.now() + delayMs),
    onConnected: () => pool().streamLive(entry.id),
    onThreadId: (threadId) => {
      runTracker.bindThread(threadId)
      pool().rekey(entry.id, threadId)
    },
    onCreated: () => {
      runTracker.created()
      setIsOffloading(false)
      pool().runAccepted(entry.id)
      if (cloud) invalidateAgentThreadLists(queryClient)
    },
    onCompleted: (info) => {
      runTracker.completed(info.reason)
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
      if (payload?.type === "model_routed") {
        setRouted({
          route: typeof payload.route === "string" ? payload.route : undefined,
          modelId:
            typeof payload.model_id === "string" ? payload.model_id : null,
        })
      }
    },
    onError: () => setIsOffloading(false),
  })

  useChannelEffect(stream, ["lifecycle", "messages"], {
    onEvent: (event) => runTracker.event(event),
  })

  // Every send goes through the published handle, so timing it here covers
  // the composer, the home page and the local queue alike.
  const submit = useCallback<AgentStream["submit"]>(
    (...args) => {
      runTracker.submitted()
      return stream.submit(...args)
    },
    [runTracker, stream]
  )

  const publish = useStreamPool((state) => state.publish)
  useLayoutEffect(
    () => publish(entry.id, { ...stream, submit, isOffloading, routed }),
    [entry.id, publish, stream, submit, isOffloading, routed]
  )

  useEffect(() => {
    if (!stream.isLoading) pool().streamLive(entry.id)
  }, [entry.id, pool, stream.isLoading])

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
        <PooledStream key={entry.id} entry={entry} />
      ))}
      {stream && (
        <AgentStreamContext.Provider value={stream}>
          {children}
        </AgentStreamContext.Provider>
      )}
    </>
  )
}

/** Liveness of the bound thread's event stream. */
export function useAgentStreamConnection(
  transport: AgentThreadTransport,
  threadId: string | null
): StreamConnection {
  return useStreamPool((state) =>
    selectConnectionFor(state, transport, threadId)
  )
}
