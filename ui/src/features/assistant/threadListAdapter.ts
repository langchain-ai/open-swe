import type { QueryClient } from "@tanstack/react-query"
import type { RemoteThreadListAdapter } from "@assistant-ui/react"
import { agentsApi } from "@/features/agents/lib/api"
import {
  beginAgentThreadUpdate,
  invalidateAgentThreadLists,
  restoreAgentThreadQueries,
  setAgentThreadResolved,
  setAgentThreadTitle,
  storeAgentThread,
} from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { reportError } from "@/lib/errorReporting"

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
  const updateAgentThread = async (
    id: string,
    errorTitle: string,
    apply: () => void,
    request: () => Promise<AgentThread>
  ) => {
    const update = await beginAgentThreadUpdate(client, id, apply)
    try {
      const thread = await request()
      client.setQueryData(threadKey(id), thread)
      storeAgentThread(client, thread)
    } catch (error) {
      restoreAgentThreadQueries(client, update)
      reportError({ title: errorTitle, error })
      throw error
    } finally {
      invalidateAgentThreadLists(client)
    }
  }

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
      await updateAgentThread(
        id,
        "Couldn't rename thread",
        () => setAgentThreadTitle(client, id, title),
        () => agentsApi.renameThread(id, title)
      )
    },
    async archive(id) {
      await updateAgentThread(
        id,
        "Couldn't archive thread",
        () => setAgentThreadResolved(client, id, true),
        () => agentsApi.resolveThread(id, true)
      )
    },
    async unarchive(id) {
      await updateAgentThread(
        id,
        "Couldn't restore thread",
        () => setAgentThreadResolved(client, id, false),
        () => agentsApi.resolveThread(id, false)
      )
    },
    async delete(id) {
      try {
        await agentsApi.deleteThread(id)
      } catch (error) {
        reportError({ title: "Couldn't delete thread", error })
        throw error
      }
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
