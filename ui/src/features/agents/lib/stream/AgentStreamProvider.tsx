import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
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
import { useStreamPool } from "./streamPool"
import type { ReactNode } from "react"
import type {
  AgentStream,
  AgentThreadTransport,
  StreamPoolEntry,
} from "./streamPool"

export type { AgentStream, AgentThreadTransport } from "./streamPool"

const AGENT_ASSISTANT_ID = "agent"
const SWEEP_INTERVAL_MS = 10_000

const AgentStreamContext = createContext<AgentStream | null>(null)

export function useAgentStream(): AgentStream {
  const stream = useContext(AgentStreamContext)
  if (!stream) throw new Error("useAgentStream requires AgentStreamProvider")
  return stream
}

function PooledStream({
  entry,
  onThreadCreated,
}: {
  entry: StreamPoolEntry
  onThreadCreated: (threadId: string) => void
}) {
  const queryClient = useQueryClient()
  const client = useMemo(
    () =>
      entry.transport === "local"
        ? createLocalGraphClient()
        : createDashboardClient(agentsApi.langGraphApiUrl),
    [entry.transport]
  )
  // The SDK captures these once per controller; they read live pool state.
  const lifecycle = useMemo(() => {
    const currentThreadId = () =>
      useStreamPool.getState().entries.find((e) => e.id === entry.id)?.threadId
    const cloud = entry.transport === "cloud"
    // Only the first accepted run of a lazily created thread is a creation;
    // follow-ups on retained background threads must not steal navigation.
    let awaitingCreation = entry.threadId === null
    return {
      onThreadId: (threadId: string) =>
        useStreamPool.getState().rekey(entry.id, threadId),
      onCreated: () => {
        if (!cloud) return
        const threadId = currentThreadId()
        const active = useStreamPool.getState().activeId === entry.id
        if (awaitingCreation && threadId && active) onThreadCreated(threadId)
        awaitingCreation = false
        invalidateAgentThreadLists(queryClient)
      },
      onCompleted: () => {
        if (!cloud) return
        const threadId = currentThreadId()
        if (threadId) {
          void queryClient.invalidateQueries({
            queryKey: agentThreadKeys.detail(threadId),
          })
        }
        invalidateAgentThreadLists(queryClient)
      },
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- threadId is read at construction only
  }, [entry.id, entry.transport, onThreadCreated, queryClient])

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId: entry.threadId,
    fetch: dashboardFetch,
    ...lifecycle,
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
  useLayoutEffect(
    () => activate(transport, threadId),
    [activate, threadId, transport]
  )
  useEffect(() => {
    const timer = setInterval(() => sweep(Date.now()), SWEEP_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [sweep])

  const onThreadCreatedRef = useRef(onThreadCreated)
  useEffect(() => {
    onThreadCreatedRef.current = onThreadCreated
  }, [onThreadCreated])
  const announceThread = useCallback((id: string) => {
    onThreadCreatedRef.current?.(id)
  }, [])

  const entries = useStreamPool((state) => state.entries)
  // Resolve the handle from the route's own request, never from whatever was
  // active last: a retained thread is served in the same render it is asked
  // for, and a not-yet-bound one renders nothing until `activate` runs.
  const active = useStreamPool((state) => {
    const retained =
      threadId !== null
        ? state.entries.find(
            (entry) =>
              entry.transport === transport && entry.threadId === threadId
          )
        : undefined
    const bound =
      state.binding?.transport === transport &&
      state.binding.threadId === threadId
        ? state.entries.find((entry) => entry.id === state.activeId)
        : undefined
    const entry = retained ?? bound
    return entry ? state.handles[entry.id] : undefined
  })

  return (
    <>
      {entries.map((entry) => (
        <PooledStream
          key={entry.id}
          entry={entry}
          onThreadCreated={announceThread}
        />
      ))}
      {active && (
        <AgentStreamContext.Provider value={active}>
          {children}
        </AgentStreamContext.Provider>
      )}
    </>
  )
}
