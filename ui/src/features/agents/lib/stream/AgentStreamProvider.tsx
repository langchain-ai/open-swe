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
  absoluteApiUrl,
  createDashboardClient,
  createLocalGraphClient,
  dashboardFetch,
} from "@/lib/langgraph-client"
import { RunTracker } from "@/lib/perf/streaming"
import { withLazyHydration } from "./lazyHydration"
import {
  MAX_RECONNECT_ATTEMPTS,
  reconnectDelayMs,
  useStreamConnection,
} from "./streamConnection"
import { useReconnectNotice } from "./useReconnectNotice"
import type { ReactNode } from "react"
import type {
  AgentStream,
  AgentThreadTransport,
  StreamConnection,
} from "./streamConnection"

export type {
  AgentStream,
  AgentThreadTransport,
  StreamConnection,
} from "./streamConnection"

const AGENT_ASSISTANT_ID = "agent"

const AgentStreamContext = createContext<AgentStream | null>(null)

export function useAgentStream(): AgentStream {
  const stream = useContext(AgentStreamContext)
  if (!stream) throw new Error("useAgentStream requires AgentStreamProvider")
  return stream
}

interface HostProps {
  threadId: string | null
  transport: AgentThreadTransport
  onThreadCreated?: (threadId: string) => void
  children: ReactNode
}

/**
 * The one SDK stream under `/agents`. Its `threadId` follows the route: the
 * SDK swaps threads in place, tearing down the previous subscription and
 * hydrating the next one, so switching threads is a fresh (skeleton) fetch
 * rather than a retained instance.
 */
function AgentStreamHost({
  threadId,
  transport,
  onThreadCreated,
  children,
}: HostProps) {
  const queryClient = useQueryClient()
  const cloud = transport === "cloud"
  const client = useMemo(
    () =>
      cloud
        ? withLazyHydration(
            createDashboardClient(agentsApi.langGraphApiUrl),
            absoluteApiUrl(agentsApi.langGraphApiUrl),
            dashboardFetch
          )
        : createLocalGraphClient(),
    [cloud]
  )
  const { schedule: scheduleReconnectNotice, clear: clearReconnectNotice } =
    useReconnectNotice()
  const [runTracker] = useState(() => new RunTracker({ transport, threadId }))
  useEffect(() => () => runTracker.dispose(), [runTracker])
  useEffect(() => {
    if (threadId) runTracker.bindThread(threadId)
  }, [runTracker, threadId])
  const [isOffloading, setIsOffloading] = useState(false)
  const [routed, setRouted] = useState<{
    route?: string
    modelId?: string | null
  } | null>(null)

  // What the route currently shows, readable from SDK callbacks without a
  // stale closure; and the id a thread-less stream minted on its first send.
  const boundThreadId = useRef(threadId)
  useLayoutEffect(() => {
    boundThreadId.current = threadId
  }, [threadId])
  const mintedThreadId = useRef<string | null>(null)

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId,
    fetch: dashboardFetch,
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    reconnectDelayMs,
    onReconnect: scheduleReconnectNotice,
    onConnected: clearReconnectNotice,
    onThreadId: (id) => {
      runTracker.bindThread(id)
      if (boundThreadId.current === null) mintedThreadId.current = id
    },
    onCreated: () => {
      runTracker.created()
      setIsOffloading(false)
      if (!cloud) return
      invalidateAgentThreadLists(queryClient)
      const minted = mintedThreadId.current
      mintedThreadId.current = null
      // Only a thread the user is still looking at may steer navigation; a
      // draft they left behind finishes creating quietly.
      if (minted && boundThreadId.current === null) onThreadCreated?.(minted)
    },
    onCompleted: (info) => {
      runTracker.completed(info.reason)
      setIsOffloading(false)
      if (!cloud) return
      const id = boundThreadId.current ?? mintedThreadId.current
      if (id) {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.detail(id),
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

  const handle = useMemo<AgentStream>(
    () => ({ ...stream, submit, isOffloading, routed }),
    [stream, submit, isOffloading, routed]
  )

  useEffect(() => {
    if (!stream.isLoading) clearReconnectNotice()
  }, [clearReconnectNotice, stream.isLoading])

  useEffect(() => {
    const thread = stream.getThread()
    if (!thread) return
    return thread.onError(clearReconnectNotice)
  }, [clearReconnectNotice, stream])

  // A thread switch leaves the previous connection's status behind.
  useEffect(() => {
    clearReconnectNotice()
  }, [clearReconnectNotice, threadId])

  return (
    <AgentStreamContext.Provider value={handle}>
      {children}
    </AgentStreamContext.Provider>
  )
}

/** Provides the stream for the thread the route asks for. */
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
  // A transport change is a different client, so it gets a fresh stream.
  return (
    <AgentStreamHost
      key={transport}
      threadId={threadId}
      transport={transport}
      onThreadCreated={onThreadCreated}
    >
      {children}
    </AgentStreamHost>
  )
}

/** Liveness of the bound stream's event subscription. */
export function useAgentStreamConnection(): StreamConnection {
  return useStreamConnection((state) => state.connection)
}
