import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import type { ReactNode } from "react"
import {
  AssistantRuntimeProvider,
  useAui,
  useAuiState,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react"
import { useChannelEffect, useStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import type { QueryClient } from "@tanstack/react-query"
import { AgentStreamContext } from "@/features/agents/lib/stream/AgentStreamProvider"
import type {
  AgentStream,
  AgentThreadTransport,
} from "@/features/agents/lib/stream/AgentStreamProvider"
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
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useConversationRuntime } from "./conversationRuntime"
import { visiblePendingMessages } from "@/features/agents/lib/queuedMessages"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  createThreadListAdapter,
  parseRuntimeThreadId,
  runtimeThreadId,
} from "./threadListAdapter"

interface RuntimeExtras {
  stream: AgentStream
}

function useAgentThreadRuntime(
  queryClient: QueryClient,
  onThreadCreated: (id: string) => void
) {
  const aui = useAui()
  const remoteId = useAuiState((s) => s.threadListItem.remoteId)
  const externalId = useAuiState((s) => s.threadListItem.externalId)
  const transport = remoteId
    ? parseRuntimeThreadId(remoteId).transport
    : "cloud"
  const cloud = transport === "cloud"
  const creating = useRef(!remoteId)
  const [isOffloading, setIsOffloading] = useState(false)
  const client = useMemo(
    () =>
      cloud
        ? createDashboardClient(agentsApi.langGraphApiUrl)
        : createLocalGraphClient(),
    [cloud]
  )
  const stream = useStream({
    client,
    assistantId: "agent",
    threadId: externalId ?? null,
    fetch: dashboardFetch,
    onCreated: () => {
      setIsOffloading(false)
      const item = aui.threadListItem().getState()
      if (cloud) invalidateAgentThreadLists(queryClient)
      if (creating.current && item.externalId) {
        creating.current = false
        if (cloud && aui.threads().getState().mainThreadId === item.id)
          onThreadCreated?.(item.externalId)
      }
    },
    onCompleted: () => {
      setIsOffloading(false)
      const id = aui.threadListItem().getState().externalId
      if (cloud && id) {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.detail(id),
        })
        invalidateAgentThreadLists(queryClient)
      }
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
  const submit = useCallback<AgentStream["submit"]>(
    async (values, options) => {
      const { externalId: id } = await aui.threadListItem().initialize()
      await stream.submit(values, { ...options, threadId: id })
    },
    [aui, stream]
  )
  const handle = useMemo(
    () => ({ ...stream, submit, isOffloading }),
    [stream, submit, isOffloading]
  )
  const baseMessages = useMemo(
    () =>
      streamMessagesToUi(
        stream.messages,
        stream.toolCalls,
        messageArrivalTimestamp
      ).filter((message) => !message.hidden),
    [stream.messages, stream.toolCalls]
  )
  const subscribeToThread = useCallback(
    (changed: () => void) =>
      queryClient.getQueryCache().subscribe((event) => {
        if (
          event.query.queryKey[0] === "agent-threads" &&
          event.query.queryKey[1] === externalId
        )
          changed()
      }),
    [queryClient, externalId]
  )
  const getThread = useCallback(
    () =>
      cloud && externalId
        ? queryClient.getQueryData<AgentThread>(
            agentThreadKeys.detail(externalId)
          )
        : undefined,
    [queryClient, cloud, externalId]
  )
  const thread = useSyncExternalStore(subscribeToThread, getThread, getThread)
  const messages = useMemo(
    () => [
      ...baseMessages,
      ...visiblePendingMessages(thread?.pendingMessages, baseMessages),
    ],
    [baseMessages, thread?.pendingMessages]
  )
  return useConversationRuntime({
    messages,
    isStreaming: stream.isLoading || thread?.status === "running",
    isLoading: stream.isThreadLoading,
    extras: { stream: handle } satisfies RuntimeExtras,
  })
}

function StreamBinding({
  threadId,
  children,
}: {
  threadId?: string
  children: ReactNode
}) {
  const selectedId = useAuiState((s) => s.threadListItem.remoteId)
  const extras = useAuiState((s) => s.thread.extras) as
    | RuntimeExtras
    | undefined
  if (!extras?.stream || (threadId && selectedId !== threadId)) return null
  return (
    <AgentStreamContext.Provider value={extras.stream}>
      {children}
    </AgentStreamContext.Provider>
  )
}

export function ExperimentalRuntimeProvider({
  threadId,
  transport = "cloud",
  cloudEnabled,
  onThreadCreated,
  children,
}: {
  threadId: string | null
  transport?: AgentThreadTransport
  cloudEnabled: boolean
  onThreadCreated?: (id: string) => void
  children: ReactNode
}) {
  const route = useRef({ threadId, onThreadCreated })
  useLayoutEffect(() => {
    route.current = { threadId, onThreadCreated }
  }, [threadId, onThreadCreated])
  const announceCreated = useCallback((id: string) => {
    if (!route.current.threadId) route.current.onThreadCreated?.(id)
  }, [])
  const queryClient = useQueryClient()
  const runtimeHook = useMemo(
    () =>
      function useRuntime() {
        return useAgentThreadRuntime(queryClient, announceCreated)
      },
    [queryClient, announceCreated]
  )
  const [unavailableThread, setUnavailableThread] = useState<string>()
  const adapter = useMemo(
    () => createThreadListAdapter(cloudEnabled, setUnavailableThread),
    [cloudEnabled]
  )
  const selectedId = threadId ? runtimeThreadId(transport, threadId) : undefined
  const runtime = useRemoteThreadListRuntime({
    adapter,
    runtimeHook,
    threadId: selectedId,
  })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {selectedId && unavailableThread === selectedId ? (
        <div role="alert" className="p-6 text-sm">
          Could not load this conversation.{" "}
          <a href="/agents" className="underline">
            Return to threads
          </a>
        </div>
      ) : (
        <StreamBinding threadId={selectedId}>{children}</StreamBinding>
      )}
    </AssistantRuntimeProvider>
  )
}
