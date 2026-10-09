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
import { createWebClient, webFetch } from "@/lib/langgraph-client"
import { useSession } from "@/lib/session"
import {
  createThreadListAdapter,
  threadKey,
  threadQuery,
} from "./threadListAdapter"
import { toolkit } from "./Message"
import { useModelSelectionSubmission } from "./useModelSelectionSubmission"
import { createRunStartTracker } from "./runStartTracker"

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
  const client = useMemo(() => createWebClient(agentsApi.langGraphApiUrl), [])
  const runStarts = useMemo(() => createRunStartTracker(webFetch), [])
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

  const runtime = useStreamRuntime({
    client,
    assistantId: "agent",
    fetch: runStarts.fetch,
    autoCancelPendingToolCalls: false,
    isDisabled: !canPost || (exists && !metadata.data),
    adapters: { attachments },
    onCreated: ({ runId }) => {
      acceptModelSelection(runId)
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
  const acceptModelSelection = useModelSelectionSubmission(runtime, runStarts)
  return runtime
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
