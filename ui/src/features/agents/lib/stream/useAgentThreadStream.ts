import { useCallback, useEffect, useMemo, useState } from "react"
import { useChannelEffect, useStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"

import { agentsApi } from "@/features/agents/lib/api"
import { orderRunStarts } from "@/features/agents/lib/runStartOrder"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
} from "@/features/agents/lib/queries"
import { createDashboardClient, dashboardFetch } from "@/lib/langgraph-client"
import { RunTracker } from "@/lib/perf/streaming"
import { MAX_RECONNECT_ATTEMPTS, reconnectDelayMs } from "./connection"
import { useReconnectNotice } from "./useReconnectNotice"
import { createRunAcceptanceTracker } from "./runAcceptance"
import type { RunAcceptance } from "./runAcceptance"
import type { AgentStream, RoutedModel, StreamConnection } from "./connection"

const AGENT_ASSISTANT_ID = "agent"

export interface AgentThreadStream {
  stream: AgentStream
  connection: StreamConnection
  trackRunAcceptance: (
    messageId: string | null,
    onAccepted?: () => void,
    onRejected?: (error: unknown) => void
  ) => () => Promise<RunAcceptance>
}

/**
 * The SDK stream serving one thread page, which must already exist: the thread's
 * first message, which creates it, goes through `/commands`. Unmounting only
 * detaches this client — the run keeps going server-side and is joined again on
 * the next visit — so nothing needs to outlive the page.
 */
export function useAgentThreadStream(threadId: string): AgentThreadStream {
  const queryClient = useQueryClient()
  const [runAcceptance] = useState(() =>
    createRunAcceptanceTracker(dashboardFetch)
  )
  // Every request of the hook, run starts included, goes through the client's fetch.
  const [client] = useState(() =>
    createDashboardClient(
      agentsApi.langGraphApiUrl,
      orderRunStarts(runAcceptance.fetch)
    )
  )
  const { connection, onReconnect, onConnected } = useReconnectNotice()
  const [runTracker] = useState(
    () => new RunTracker({ threadId, threadSource: "stream" })
  )
  useEffect(() => () => runTracker.dispose(), [runTracker])
  const [isOffloading, setIsOffloading] = useState(false)
  const [routed, setRouted] = useState<RoutedModel | null>(null)

  const stream = useStream({
    client,
    assistantId: AGENT_ASSISTANT_ID,
    threadId,
    // A message sent during a run waits in the server's queue as a run of its own.
    queue: "server",
    // The view reads only messages, so opening a thread skips the agent's large state keys.
    stateKeys: [],
    maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
    reconnectDelayMs,
    onReconnect,
    onConnected,
    onCreated: () => {
      runTracker.created()
      setIsOffloading(false)
      invalidateAgentThreadLists(queryClient)
    },
    onCompleted: (info) => {
      runTracker.completed(info.reason)
      setIsOffloading(false)
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
      if (!isRecord(payload)) return
      if (payload.type === "conversation_offloading") {
        setIsOffloading(payload.status === "started")
      }
      if (payload.type === "model_routed") {
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

  // A stream that gives up reconnecting fails with an error; there is nothing left to wait for.
  const failed = stream.error != null
  useEffect(() => {
    if (failed) onConnected()
  }, [failed, onConnected])

  return useMemo(
    () => ({
      stream: { ...stream, submit, isOffloading, routed },
      connection,
      trackRunAcceptance: runAcceptance.track,
    }),
    [connection, isOffloading, routed, runAcceptance, stream, submit]
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
