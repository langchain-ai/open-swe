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
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { agentsApi } from "@/features/agents/lib/api"
import { createDashboardClient, dashboardFetch } from "@/lib/langgraph-client"
import { useSession } from "@/lib/session"
import {
  createThreadListAdapter,
  threadKey,
  threadQuery,
} from "./threadListAdapter"
import { toolkit } from "./Message"

export function useThreadMetadata() {
  const id = useAuiState((state) => state.threadListItem.externalId)
  // The thread runtime owns fetching; views observe its cache.
  return useQuery({ ...threadQuery(id ?? ""), enabled: false })
}

function RuntimeReady({ children }: { children: ReactNode }) {
  const ready = useAuiState((state) => state.thread.extras !== undefined)
  return ready ? children : <p role="status">Loading conversation…</p>
}

function useOpenSweThreadRuntime() {
  const aui = useAui()
  const id = useAuiState((state) => state.threadListItem.externalId)
  const [exists, setExists] = useState(Boolean(id))
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
    onCreated: () => {
      setExists(true)
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
