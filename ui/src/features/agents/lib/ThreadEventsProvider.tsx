import { useEffect, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useStreamContext } from "@langchain/react"

import { agentsApi } from "./api"
import { agentThreadKeys } from "./queries"
import type { AgentThread } from "./types"
import type { ReactNode } from "react"
import type { InfiniteData, QueryClient, QueryKey } from "@tanstack/react-query"

interface ThreadEventEnvelope {
  id: number
  thread_id: string
  kind: string
  payload: Record<string, unknown>
}

interface IdPage {
  ids: Array<string>
  limit: number
  cursor?: string | null
  hasMore?: boolean
}

function matchesList(thread: AgentThread, key: QueryKey): boolean {
  const params = key[2]
  if (!params || typeof params !== "object") return true
  const filters = params as Record<string, unknown>
  if (
    typeof filters.resolved === "boolean" &&
    Boolean(thread.resolved) !== filters.resolved
  )
    return false
  if (filters.environment && thread.environment !== filters.environment)
    return false
  if (filters.source && thread.source !== filters.source) return false
  if (filters.status && thread.status !== filters.status) return false
  if (filters.scope === "interactive" && thread.threadCategory === "automation")
    return false
  if (filters.scope === "automation" && thread.threadCategory !== "automation")
    return false
  if (
    typeof filters.q === "string" &&
    !thread.title.toLowerCase().includes(filters.q.toLowerCase())
  )
    return false
  return true
}

function reconcileListMembership(
  queryClient: QueryClient,
  thread: AgentThread,
  deleted = false
) {
  const queries = queryClient.getQueryCache().findAll({
    queryKey: agentThreadKeys.lists,
  })
  for (const query of queries) {
    const key = query.queryKey
    if (key[1] === "infinite-pages") {
      queryClient.setQueryData<InfiniteData<IdPage>>(key, (current) => {
        if (!current) return current
        const include = !deleted && matchesList(thread, key)
        const pages = current.pages.map((page, index) => ({
          ...page,
          ids: [
            ...(include && index === 0 ? [thread.id] : []),
            ...page.ids.filter((id) => id !== thread.id),
          ],
        }))
        return { ...current, pages }
      })
    } else if (key[1] === "page") {
      queryClient.setQueryData<IdPage>(key, (current) => {
        if (!current) return current
        const include = !deleted && matchesList(thread, key)
        return {
          ...current,
          ids: [
            ...(include ? [thread.id] : []),
            ...current.ids.filter((id) => id !== thread.id),
          ],
        }
      })
    }
  }
}

export function ThreadEventsProvider({
  activeThreadId,
  children,
}: {
  activeThreadId?: string
  children: ReactNode
}) {
  const queryClient = useQueryClient()
  const stream = useStreamContext()
  const streamLoading = useRef(stream.isLoading)
  const cursor = useRef(0)

  useEffect(() => {
    streamLoading.current = stream.isLoading
  }, [stream.isLoading])

  useEffect(() => {
    const url = new URL(agentsApi.eventsUrl, window.location.origin)
    if (cursor.current) url.searchParams.set("cursor", String(cursor.current))
    const source = new EventSource(url, { withCredentials: true })
    const apply = (event: MessageEvent<string>) => {
      let envelope: ThreadEventEnvelope
      try {
        envelope = JSON.parse(event.data) as ThreadEventEnvelope
      } catch {
        return
      }
      cursor.current = Math.max(cursor.current, envelope.id || 0)
      const thread = envelope.payload as unknown as AgentThread
      if (
        envelope.kind === "thread.created" ||
        envelope.kind === "thread.status" ||
        envelope.kind === "thread.meta" ||
        envelope.kind === "thread.handoff"
      ) {
        queryClient.setQueryData<AgentThread>(
          agentThreadKeys.detail(envelope.thread_id),
          (current) => ({ ...current, ...thread }) as AgentThread
        )
        reconcileListMembership(queryClient, thread)
        if (
          envelope.kind === "thread.handoff" &&
          thread.environment === "local" &&
          thread.deviceId &&
          thread.deviceId === window.openSweDesktop?.deviceId &&
          thread.repoFullName &&
          thread.gitCheckpoint
        ) {
          void agentsApi
            .getThreadMessages(thread.id)
            .then((response) =>
              window.openSweDesktop?.prepareLocalHandoff({
                threadId: thread.id,
                deviceId: thread.deviceId as string,
                repoFullName: thread.repoFullName,
                gitCheckpoint: thread.gitCheckpoint as {
                  repo: string
                  ref: string
                  branch: string
                  pushed: boolean
                },
                modelId: thread.model,
                effort: thread.effort,
                messages: response.items.map((item) => item.payload),
              })
            )
            .catch(() => undefined)
        }
      } else if (envelope.kind === "thread.deleted") {
        const current = queryClient.getQueryData<AgentThread>(
          agentThreadKeys.detail(envelope.thread_id)
        )
        reconcileListMembership(
          queryClient,
          current ?? ({ id: envelope.thread_id } as AgentThread),
          true
        )
        queryClient.removeQueries({
          queryKey: agentThreadKeys.detail(envelope.thread_id),
          exact: true,
        })
      } else if (
        envelope.kind === "thread.message" &&
        envelope.thread_id === activeThreadId &&
        !streamLoading.current
      ) {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.messages(envelope.thread_id),
        })
      }
    }
    const kinds = [
      "thread.created",
      "thread.status",
      "thread.meta",
      "thread.message",
      "thread.deleted",
      "thread.handoff",
    ]
    for (const kind of kinds) source.addEventListener(kind, apply)
    return () => source.close()
  }, [activeThreadId, queryClient])

  return children
}
