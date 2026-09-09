import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react"
import { STREAM_CONTROLLER, type UseStreamReturn } from "@langchain/react"
import {
  Client,
  filterOutHeadlessToolInterrupts,
  overrideFetchImplementation,
} from "@langchain/langgraph-sdk"
import type { StreamController } from "@langchain/langgraph-sdk/stream"
import { AgentThreadRuntime } from "./AgentThreadRuntime"
import { useQueryClient, type QueryClient } from "@tanstack/react-query"

import { agentsApi } from "./api"
import { agentThreadKeys, invalidateAgentThreadLists } from "./queries"
import type { ReactNode } from "react"

type AgentThreadTransport = "cloud" | "local"

interface RuntimeEntry {
  key: string
  transport: AgentThreadTransport
  client: Client<Record<string, unknown>>
  controller: StreamController
  runtime: AgentThreadRuntime
  createdThreadCallbacks: Set<(threadId: string) => void>
  deactivate: () => void
  disposeTimer?: ReturnType<typeof setTimeout>
  mounts: number
  lastUsedAt: number
}

const AGENT_ASSISTANT_ID = "agent"
const IDLE_RUNTIME_TTL_MS = 60_000
const MAX_RETAINED_RUNTIMES = 8
const runtimeEntries = new Map<string, RuntimeEntry>()

const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" })

overrideFetchImplementation(dashboardFetch)

const dashboardRequest = (_url: URL, init: RequestInit): RequestInit => ({
  ...init,
  credentials: "include",
})

function toAbsoluteApiUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url
  if (typeof window !== "undefined") {
    return `${window.location.origin}${url.startsWith("/") ? "" : "/"}${url}`
  }
  return url
}

function runtimeKey(
  transport: AgentThreadTransport,
  threadId: string | null
): string {
  return `${transport}:${threadId ?? "new"}`
}

function disposeRuntime(entry: RuntimeEntry): void {
  if (runtimeEntries.get(entry.key) !== entry) return
  runtimeEntries.delete(entry.key)
  if (entry.disposeTimer) clearTimeout(entry.disposeTimer)
  entry.deactivate()
}

function scheduleRuntimeDisposal(entry: RuntimeEntry): void {
  if (entry.mounts > 0) {
    clearTimeout(entry.disposeTimer)
    entry.disposeTimer = undefined
    return
  }
  if (entry.disposeTimer) return
  entry.disposeTimer = setTimeout(
    () => disposeRuntime(entry),
    IDLE_RUNTIME_TTL_MS
  )
}

function trimRuntimeRegistry(protectedEntry?: RuntimeEntry): void {
  if (runtimeEntries.size <= MAX_RETAINED_RUNTIMES) return
  const idle = [...runtimeEntries.values()]
    .filter((entry) => entry !== protectedEntry && entry.mounts === 0)
    .sort((left, right) => left.lastUsedAt - right.lastUsedAt)
  for (const entry of idle) {
    if (runtimeEntries.size <= MAX_RETAINED_RUNTIMES) break
    disposeRuntime(entry)
  }
}

function rekeyRuntime(entry: RuntimeEntry, threadId: string): void {
  const nextKey = runtimeKey(entry.transport, threadId)
  if (entry.key === nextKey) return
  if (runtimeEntries.get(entry.key) === entry) runtimeEntries.delete(entry.key)
  const existing = runtimeEntries.get(nextKey)
  if (existing && existing !== entry) disposeRuntime(existing)
  entry.key = nextKey
  runtimeEntries.set(nextKey, entry)
}

function createRuntime(
  transport: AgentThreadTransport,
  threadId: string | null,
  queryClient: QueryClient
): RuntimeEntry {
  const apiUrl = toAbsoluteApiUrl(
    transport === "local" ? "/local-graph" : agentsApi.langGraphApiUrl
  )
  const client = new Client<Record<string, unknown>>({
    apiUrl,
    apiKey: null,
    timeoutMs: 15_000,
    ...(transport === "cloud" ? { onRequest: dashboardRequest } : {}),
  })
  let entry: RuntimeEntry
  const runtime = new AgentThreadRuntime({
    transport,
    fetch: dashboardFetch,
    client,
    threadId,
    onThreadId: (id) => {
      rekeyRuntime(entry, id)
    },
    onCreated: () => {
      if (transport !== "cloud") return
      const id = entry.controller.rootStore.getSnapshot().threadId
      if (id) {
        for (const callback of entry.createdThreadCallbacks) callback(id)
      }
      invalidateAgentThreadLists(queryClient)
    },
    onCompleted: () => {
      if (transport !== "cloud") return
      const id = entry.controller.rootStore.getSnapshot().threadId
      if (id) {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.detail(id),
        })
      }
      invalidateAgentThreadLists(queryClient)
    },
  })
  entry = {
    key: runtimeKey(transport, threadId),
    transport,
    client,
    controller: runtime.controller,
    runtime,
    createdThreadCallbacks: new Set(),
    deactivate: () => runtime.dispose(),
    mounts: 0,
    lastUsedAt: Date.now(),
  }
  runtime.store.subscribe(() => {
    scheduleRuntimeDisposal(entry)
    trimRuntimeRegistry()
  })
  runtimeEntries.set(entry.key, entry)
  scheduleRuntimeDisposal(entry)
  trimRuntimeRegistry(entry)
  return entry
}

function retainRuntime(entry: RuntimeEntry): () => void {
  entry.mounts += 1
  entry.lastUsedAt = Date.now()
  scheduleRuntimeDisposal(entry)
  return () => {
    entry.mounts -= 1
    entry.lastUsedAt = Date.now()
    scheduleRuntimeDisposal(entry)
  }
}

export type AgentThreadStream = UseStreamReturn & {
  connection: "connecting" | "connected" | "recovering"
  refresh: () => Promise<void>
  enqueue: AgentThreadRuntime["enqueue"]
  queuedMessages: ReturnType<
    AgentThreadRuntime["store"]["getSnapshot"]
  >["queuedMessages"]
}

function useRuntimeStream(entry: RuntimeEntry): AgentThreadStream {
  const { controller } = entry
  const root = useSyncExternalStore(
    entry.runtime.store.subscribe,
    entry.runtime.store.getSnapshot,
    entry.runtime.store.getSnapshot
  )
  const subagents = useSyncExternalStore(
    controller.subagentStore.subscribe,
    controller.subagentStore.getSnapshot,
    controller.subagentStore.getSnapshot
  )
  const subgraphs = useSyncExternalStore(
    controller.subgraphStore.subscribe,
    controller.subgraphStore.getSnapshot,
    controller.subgraphStore.getSnapshot
  )
  const subgraphsByNode = useSyncExternalStore(
    controller.subgraphByNodeStore.subscribe,
    controller.subgraphByNodeStore.getSnapshot,
    controller.subgraphByNodeStore.getSnapshot
  )

  return useMemo(() => {
    const interrupts = filterOutHeadlessToolInterrupts(root.interrupts)
    return {
      values: root.values,
      messages: root.messages,
      toolCalls: root.toolCalls,
      interrupts,
      interrupt: interrupts[0],
      isLoading: root.isLoading,
      isThreadLoading: root.isThreadLoading,
      hydrationPromise: controller.hydrationPromise,
      error: root.error,
      threadId: root.threadId,
      subagents,
      subgraphs,
      subgraphsByNode,
      connection: root.connection,
      refresh: entry.runtime.refresh,
      queuedMessages: root.queuedMessages,
      enqueue: entry.runtime.enqueue,
      submit: entry.runtime.submit,
      stop: entry.runtime.stop,
      disconnect: () => controller.disconnect(),
      respond: entry.runtime.respond,
      respondAll: entry.runtime.respondAll,
      getThread: () => controller.getThread(),
      client: entry.client,
      assistantId: AGENT_ASSISTANT_ID,
      [STREAM_CONTROLLER]: controller,
    } as AgentThreadStream
  }, [controller, entry, root, subagents, subgraphs, subgraphsByNode])
}

const AgentThreadRuntimeContext = createContext<AgentThreadStream | null>(null)

export function useAgentThreadRuntime(): AgentThreadStream {
  const stream = useContext(AgentThreadRuntimeContext)
  if (!stream) throw new Error("Agent thread runtime is not available")
  return stream
}

export function AgentThreadStreamProvider({
  threadId,
  children,
  transport = "cloud",
  onThreadCreated,
}: {
  threadId: string | null
  children: ReactNode
  transport?: AgentThreadTransport
  onThreadCreated?: (threadId: string) => void
}) {
  const queryClient = useQueryClient()
  const entry = useMemo(
    () =>
      runtimeEntries.get(runtimeKey(transport, threadId)) ??
      createRuntime(transport, threadId, queryClient),
    [queryClient, threadId, transport]
  )
  const stream = useRuntimeStream(entry)

  useEffect(() => retainRuntime(entry), [entry])
  useEffect(() => {
    if (!onThreadCreated) return
    entry.createdThreadCallbacks.add(onThreadCreated)
    return () => {
      entry.createdThreadCallbacks.delete(onThreadCreated)
    }
  }, [entry, onThreadCreated])

  return (
    <AgentThreadRuntimeContext.Provider value={stream}>
      {children}
    </AgentThreadRuntimeContext.Provider>
  )
}

export const __testing = {
  entries: runtimeEntries,
  disposeAll(): void {
    for (const entry of runtimeEntries.values()) disposeRuntime(entry)
  },
}
