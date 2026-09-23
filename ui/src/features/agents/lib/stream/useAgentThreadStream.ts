import { useCallback, useEffect, useMemo, useState } from "react"
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
import { MAX_RECONNECT_ATTEMPTS, reconnectDelayMs } from "./connection"
import { useReconnectNotice } from "./useReconnectNotice"
import type {
  AgentStream,
  AgentThreadTransport,
  RoutedModel,
  StreamConnection,
} from "./connection"

const AGENT_ASSISTANT_ID = "agent"

export interface AgentThreadStream {
  stream: AgentStream
  connection: StreamConnection
}

/**
 * The SDK stream serving one thread page. Unmounting only detaches this
 * client — the run keeps going server-side and is picked up again from the
 * thread's history on the next visit — so nothing needs to outlive the page.
 */
export function useAgentThreadStream({
  transport,
  threadId,
}: {
  transport: AgentThreadTransport
  threadId: string
}): AgentThreadStream {
  const queryClient = useQueryClient()
  const cloud = transport === "cloud"
  const client = useMemo(
    () =>
      cloud
        ? createDashboardClient(agentsApi.langGraphApiUrl)
        : createLocalGraphClient(),
    [cloud]
  )
  const { connection, onReconnect, onConnected } = useReconnectNotice()
  const [runTracker] = useState(() => new RunTracker({ transport, threadId }))
  useEffect(() => () => runTracker.dispose(), [runTracker])
  const [isOffloading, setIsOffloading] = useState(false)
  const [routed, setRouted] = useState<RoutedModel | null>(null)

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId,
    fetch: dashboardFetch,
    // Only affects "stream"-kind threads; transcript threads never call
    // useStream() at all.
    queue: "server",
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    reconnectDelayMs,
    onReconnect,
    onConnected,
    onCreated: () => {
      runTracker.created()
      setIsOffloading(false)
      if (cloud) invalidateAgentThreadLists(queryClient)
    },
    onCompleted: (info) => {
      runTracker.completed(info.reason)
      setIsOffloading(false)
      if (!cloud) return
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.detail(threadId),
      })
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

  // Every send goes through the handle this hook returns, so timing it here
  // covers the composer and the local prompt queue alike.
  const submit = useCallback<AgentStream["submit"]>(
    (...args) => {
      runTracker.submitted()
      return stream.submit(...args)
    },
    [runTracker, stream]
  )

  const isLoading = stream.isLoading
  useEffect(() => {
    if (!isLoading) onConnected()
  }, [isLoading, onConnected])

  useEffect(() => {
    const thread = stream.getThread()
    if (!thread) return
    return thread.onError(onConnected)
  }, [onConnected, stream])

  return useMemo(
    () => ({
      stream: { ...stream, submit, isOffloading, routed },
      connection,
    }),
    [connection, isOffloading, routed, stream, submit]
  )
}
