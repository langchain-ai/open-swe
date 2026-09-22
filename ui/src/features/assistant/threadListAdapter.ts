import type { QueryClient } from "@tanstack/react-query"
import type { RemoteThreadListAdapter } from "@assistant-ui/react"
import { agentsApi } from "@/features/agents/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"

export const threadKey = (id: string) => ["assistant-threads", id] as const

export function threadQuery(id: string) {
  return {
    queryKey: threadKey(id),
    queryFn: () => agentsApi.getThread(id),
    staleTime: 15_000,
  }
}

function metadata(thread: AgentThread) {
  return {
    status: thread.resolved ? ("archived" as const) : ("regular" as const),
    remoteId: thread.id,
    externalId: thread.id,
    title: thread.title,
  }
}

export function createThreadListAdapter(
  client: QueryClient
): RemoteThreadListAdapter {
  return {
    async list(params) {
      const page = await agentsApi.listThreadsPage({
        offset: Number(params?.after ?? 0),
        limit: 100,
        all: true,
      })
      return {
        threads: page.items.map(metadata),
        nextCursor: page.hasMore ? String(page.offset + page.limit) : undefined,
      }
    },
    async fetch(id) {
      return metadata(await client.ensureQueryData(threadQuery(id)))
    },
    async initialize() {
      const id = crypto.randomUUID()
      return { remoteId: id, externalId: id }
    },
    async rename(id, title) {
      const thread = await agentsApi.renameThread(id, title)
      client.setQueryData(threadKey(id), thread)
    },
    async archive(id) {
      const thread = await agentsApi.resolveThread(id, true)
      client.setQueryData(threadKey(id), thread)
    },
    async unarchive(id) {
      const thread = await agentsApi.resolveThread(id, false)
      client.setQueryData(threadKey(id), thread)
    },
    async delete(id) {
      await agentsApi.deleteThread(id)
      client.removeQueries({ queryKey: threadKey(id) })
    },
    async generateTitle() {
      return new ReadableStream({
        start(controller) {
          controller.close()
        },
      })
    },
  }
}
