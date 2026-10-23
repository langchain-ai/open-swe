import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
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
import { classifyStreamError } from "@/features/agents/lib/networkRecovery"
import { useReconnectNotice } from "./useReconnectNotice"
import {
  MAX_RECONNECT_ATTEMPTS,
  STALL_QUIET_MS,
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
const STALL_CHECK_INTERVAL_MS = 5_000

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
  const networkResume = window.openSweDesktop?.networkResume
  const armedError = useRef<unknown>(null)
  const { schedule: scheduleReconnectNotice, clear: clearReconnectNotice } =
    useReconnectNotice(entry.id)
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
    onReconnect: scheduleReconnectNotice,
    onConnected: () => {
      clearReconnectNotice()
      const threadId = pool().entries.find((e) => e.id === entry.id)?.threadId
      if (!threadId) return
      // Reports that this thread's stream is serving again: a dispatching chain
      // moves to `inflight`. A waiting chain stays put — reconnecting to the
      // thread is not proof that the interrupted run survived.
      void networkResume?.working({ threadId }).catch(() => {})
    },
    onThreadId: (threadId) => {
      runTracker.bindThread(threadId)
      pool().rekey(entry.id, threadId)
    },
    onCreated: () => {
      runTracker.created()
      setIsOffloading(false)
      pool().runAccepted(entry.id)
      // The server just accepted a run, so the connection that gave up is
      // history: without this a recovered thread keeps its failure state.
      useStreamPool.getState().streamLive(entry.id)
      if (cloud) invalidateAgentThreadLists(queryClient)
    },
    onCompleted: (info) => {
      runTracker.completed(info.reason)
      setIsOffloading(false)
      const threadId = pool().entries.find((e) => e.id === entry.id)?.threadId
      if (threadId) void networkResume?.complete({ threadId }).catch(() => {})
      if (!cloud) return
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

  const liveStream = useRef(stream)
  useEffect(() => {
    liveStream.current = stream
  }, [stream])
  // Progress is whatever the transport delivers; a blackholed link delivers none.
  const lastProgressAt = useRef(Date.now())
  useChannelEffect(stream, ["messages", "updates", "lifecycle"], {
    onEvent: () => {
      lastProgressAt.current = Date.now()
    },
  })
  useEffect(() => {
    lastProgressAt.current = Date.now()
  }, [stream.messages, stream.toolCalls])

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
    if (!stream.isLoading) clearReconnectNotice()
  }, [clearReconnectNotice, stream.isLoading])

  useEffect(() => {
    const thread = stream.getThread()
    if (!thread) return
    return thread.onError(clearReconnectNotice)
  }, [clearReconnectNotice, stream])

  // A local stream that failed for transport reasons hands the turn to the
  // desktop's recovery coordinator, which alone decides when to continue.
  useEffect(() => {
    const failure = stream.error
    if (!networkResume || entry.transport !== "local" || !failure) return
    if (armedError.current === failure) return
    const threadId = pool().entries.find((e) => e.id === entry.id)?.threadId
    if (!threadId) return
    const reason = classifyStreamError(failure, navigator.onLine !== false)
    if (!reason) return
    armedError.current = failure
    void networkResume.arm({ threadId, reason }).catch(() => {})
  }, [entry.id, entry.transport, networkResume, pool, stream.error])

  // A loading local stream whose backend process exited hands the turn to the
  // coordinator immediately. The SDK's SSE transport would otherwise retry for
  // roughly 23 minutes before it reports an error, and until then nothing arms
  // the chain. `isLoading` keeps an idle thread from being armed.
  useEffect(() => {
    const bridge = networkResume
    const onBackendLost = bridge?.onBackendLost
    if (
      !bridge ||
      typeof onBackendLost !== "function" ||
      entry.transport !== "local"
    )
      return
    return onBackendLost(() => {
      if (!liveStream.current.isLoading) return
      const threadId = pool().entries.find((e) => e.id === entry.id)?.threadId
      if (!threadId) return
      void bridge.arm({ threadId, reason: "backend-down" }).catch(() => {})
    })
  }, [entry.id, entry.transport, networkResume, pool])

  // A loading local stream that stops producing events is either a quiet tool
  // call or a dead link, and only the backend can tell the two apart. Asking it
  // keeps a black hole from reading as permanent work.
  useEffect(() => {
    const stall = networkResume?.stall
    if (typeof stall !== "function" || entry.transport !== "local") return
    let checking = false
    const timer = setInterval(() => {
      if (!liveStream.current.isLoading) {
        lastProgressAt.current = Date.now()
        return
      }
      if (checking || Date.now() - lastProgressAt.current < STALL_QUIET_MS) return
      const threadId = pool().entries.find((e) => e.id === entry.id)?.threadId
      if (!threadId) return
      checking = true
      // Whatever the answer, the window restarts: one question per quiet period,
      // not one per tick.
      lastProgressAt.current = Date.now()
      void stall({ threadId })
        .then((result) => {
          if (result.action === "resync") pool().refreshStream(entry.id)
        })
        .catch(() => {})
        .finally(() => {
          checking = false
        })
    }, STALL_CHECK_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [entry.id, entry.transport, networkResume, pool])

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
        <PooledStream
          key={`${entry.id}:${entry.reconnectEpoch}`}
          entry={entry}
        />
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
