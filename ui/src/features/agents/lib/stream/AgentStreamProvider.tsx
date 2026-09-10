import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
} from "react"
import { useStream } from "@langchain/react"
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
import { trackDatadogAction } from "@/lib/datadog"
import { selectStreamFor, useStreamPool } from "./streamPool"
import type { ReactNode } from "react"
import type {
  AgentStream,
  AgentThreadTransport,
  StreamPoolEntry,
} from "./streamPool"

export type { AgentStream, AgentThreadTransport } from "./streamPool"

const AGENT_ASSISTANT_ID = "agent"
const SWEEP_INTERVAL_MS = 10_000
// The SDK stops retrying after this many failed reconnects and the instance is
// dead for good (isLoading frozen, no error). ~5 minutes of backoff covers a
// wifi blip or a backend deploy; anything longer is caught by the reconcile kick.
const MAX_RECONNECT_ATTEMPTS = 50

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

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId: entry.threadId,
    fetch: dashboardFetch,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    onReconnect: ({ attempt, cause }) => {
      trackDatadogAction("agent-stream.reconnect", {
        threadId: entry.threadId,
        attempt,
        cause: cause instanceof Error ? cause.message : String(cause),
      })
    },
    onThreadId: (threadId) => pool().rekey(entry.id, threadId),
    onCreated: () => {
      pool().runAccepted(entry.id)
      if (cloud) invalidateAgentThreadLists(queryClient)
    },
    onCompleted: () => {
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

  const publish = useStreamPool((state) => state.publish)
  useLayoutEffect(() => publish(entry.id, stream), [entry.id, publish, stream])

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
        <PooledStream key={`${entry.id}:${entry.generation}`} entry={entry} />
      ))}
      {stream && (
        <AgentStreamContext.Provider value={stream}>
          {children}
        </AgentStreamContext.Provider>
      )}
    </>
  )
}
