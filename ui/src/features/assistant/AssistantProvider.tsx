import { useMemo, useState } from "react"
import type { ReactNode } from "react"
import {
  AssistantRuntimeProvider,
  AuiConfig,
  Tools,
  SimpleImageAttachmentAdapter,
  useAui,
  useAuiState,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react"
import { useStreamRuntime } from "@assistant-ui/react-langchain"
import { STREAM_CONTROLLER } from "@langchain/react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { agentsApi } from "@/features/agents/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import { createDashboardClient, dashboardFetch } from "@/lib/langgraph-client"
import { useSession } from "@/lib/session"
import {
  createThreadListAdapter,
  threadKey,
  threadQuery,
} from "./threadListAdapter"
import { useServerQueue } from "./serverQueue"
import { toolkit } from "./Message"

export interface ProductState {
  thread?: AgentThread
  error?: string
  queueErrors: Record<string, string | undefined>
}

export function useProductState() {
  return useAuiState((state) => state.thread.extras) as ProductState
}

function RuntimeReady({ children }: { children: ReactNode }) {
  const ready = useAuiState((state) => state.thread.extras !== undefined)
  return ready ? children : <p role="status">Loading conversation…</p>
}

function useOpenSweThreadRuntime() {
  const aui = useAui()
  const id = useAuiState((state) => state.threadListItem.externalId)
  const [exists, setExists] = useState(Boolean(id))
  const [requestError, setRequestError] = useState<string>()
  const session = useSession()
  const queries = useQueryClient()
  const metadata = useQuery({
    ...threadQuery(id ?? ""),
    enabled: Boolean(id) && exists,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 3000 : false,
  })
  const client = useMemo(
    () => createDashboardClient(agentsApi.langGraphApiUrl),
    []
  )
  const { createQueueAdapter, queueErrors } = useServerQueue(id, metadata.data)
  const attachments = useMemo(() => {
    const adapter = new SimpleImageAttachmentAdapter()
    adapter.accept = "image/png,image/jpeg,image/gif,image/webp"
    return adapter
  }, [])
  const canPost =
    !metadata.data ||
    (metadata.data.threadCategory !== "automation" &&
      !metadata.data.adminThread) ||
    session.data?.is_admin === true

  return useStreamRuntime({
    client,
    assistantId: "agent",
    fetch: dashboardFetch,
    autoCancelPendingToolCalls: false,
    isDisabled: !canPost || (exists && !metadata.data),
    adapters: { attachments },
    createQueueAdapter,
    extras: {
      thread: metadata.data,
      queueErrors,
      error:
        requestError ?? (metadata.error ? metadata.error.message : undefined),
    } satisfies ProductState,
    onCreated: () => {
      setExists(true)
      setRequestError(undefined)
      const remoteId = aui.threadListItem().getState().externalId
      if (remoteId)
        void queries.invalidateQueries({ queryKey: threadKey(remoteId) })
      void aui.threads().reload()
    },
    onCompleted: () => {
      const remoteId = aui.threadListItem().getState().externalId
      if (remoteId)
        void queries.invalidateQueries({ queryKey: threadKey(remoteId) })
      void aui.threads().reload()
    },
    onCancel: async (stream) => {
      const remoteId = aui.threadListItem().getState().externalId
      if (!remoteId) return
      try {
        const thread = await agentsApi.cancelThread(remoteId)
        queries.setQueryData(threadKey(remoteId), thread)
        await stream.disconnect()
        await stream[STREAM_CONTROLLER].hydrate(remoteId)
      } catch (error) {
        setRequestError(
          error instanceof Error ? error.message : "Could not stop this run."
        )
        throw error
      }
    },
  })
}

export function AssistantProvider({
  threadId,
  onThreadChange,
  children,
}: {
  threadId?: string
  onThreadChange: (id: string | undefined) => void
  children: ReactNode
}) {
  const queries = useQueryClient()
  const adapter = useMemo(() => createThreadListAdapter(queries), [queries])
  const runtime = useRemoteThreadListRuntime({
    adapter,
    runtimeHook: useOpenSweThreadRuntime,
    threadId,
    onThreadIdChange: onThreadChange,
  })
  const config = AuiConfig({ tools: Tools({ toolkit }) })
  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <RuntimeReady>{children}</RuntimeReady>
    </AssistantRuntimeProvider>
  )
}
