import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useChannelEffect, useStream } from "@langchain/react"
import type { Client } from "@langchain/langgraph-sdk"
import { useQueryClient } from "@tanstack/react-query"

import { agentsApi } from "@/features/agents/lib/api"
import { orderRunStarts } from "@/features/agents/lib/runStartOrder"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
} from "@/features/agents/lib/queries"
import { earlyJoins } from "@/lib/earlyJoins"
import { earlyReads } from "@/lib/earlyReads"
import { createDashboardClient, dashboardFetch } from "@/lib/langgraph-client"
import { RunTracker } from "@/lib/perf/streaming"
import { ThreadJoin } from "@/lib/perf/threadLoad"
import { MAX_RECONNECT_ATTEMPTS, reconnectDelayMs } from "./connection"
import { useReconnectNotice } from "./useReconnectNotice"
import { createRunAcceptanceTracker } from "./runAcceptance"
import type { RunAcceptance } from "./runAcceptance"
import type { AgentStream, RoutedModel, StreamConnection } from "./connection"

const AGENT_ASSISTANT_ID = "agent"

export interface AgentThreadStream {
  stream: AgentStream
  client: Client
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
    createRunAcceptanceTracker(
      earlyJoins.newReader(
        earlyReads.newReader(dashboardFetch),
        (observation) =>
          ThreadJoin.of(observation.threadId)?.joined(observation)
      )
    )
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
  useEffect(
    () => () => {
      runTracker.dispose()
      ThreadJoin.of(threadId)?.abandon("unmounted")
    },
    [runTracker, threadId]
  )
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
      ThreadJoin.of(threadId)?.abandon("completed_without_event")
      setIsOffloading(false)
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.detail(threadId),
      })
      invalidateAgentThreadLists(queryClient)
    },
  })

  const isLoadingRef = useRef(stream.isLoading)
  useEffect(() => {
    isLoadingRef.current = stream.isLoading
  }, [stream.isLoading])

  useEffect(() => {
    const hydration = stream.hydrationPromise
    if (!hydration) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const onSettled = () => {
      timer = setTimeout(() => {
        if (!cancelled && !isLoadingRef.current)
          ThreadJoin.of(threadId)?.disqualify()
      }, 0)
    }
    hydration.then(onSettled, onSettled)
    return () => {
      cancelled = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [stream.hydrationPromise, threadId])

  useChannelEffect(stream, ["custom"], {
    onEvent: (event) => {
      ThreadJoin.of(threadId)?.firstEvent(event.method)
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

  useChannelEffect(stream, ["lifecycle", "messages", "updates", "values"], {
    onEvent: (event) => {
      ThreadJoin.of(threadId)?.firstEvent(event.method)
      if (event.method === "lifecycle" || event.method === "messages")
        runTracker.event(event)
    },
  })

  const submit = useCallback<AgentStream["submit"]>(
    (...args) => {
      ThreadJoin.of(threadId)?.disqualify()
      runTracker.submitted()
      return stream.submit(...args)
    },
    [runTracker, stream, threadId]
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
      client,
      connection,
      trackRunAcceptance: runAcceptance.track,
    }),
    [client, connection, isOffloading, routed, runAcceptance, stream, submit]
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
