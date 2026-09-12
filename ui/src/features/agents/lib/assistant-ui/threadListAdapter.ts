import type { RemoteThreadListAdapter } from "@assistant-ui/react"
import { agentsApi } from "@/features/agents/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AgentThreadTransport } from "@/features/agents/lib/stream/AgentStreamProvider"

export function runtimeThreadId(transport: AgentThreadTransport, id: string) {
  return `${transport}:${id}`
}

export function parseRuntimeThreadId(id: string) {
  const separator = id.indexOf(":")
  const transport = id.slice(0, separator)
  if (
    (transport !== "cloud" && transport !== "local") ||
    separator === id.length - 1
  ) {
    throw new Error("Invalid assistant thread identity")
  }
  return { transport, id: id.slice(separator + 1) }
}

function cloudMetadata(thread: AgentThread) {
  return {
    status: "regular" as const,
    remoteId: runtimeThreadId("cloud", thread.id),
    externalId: thread.id,
    title: thread.title,
  }
}

export function createThreadListAdapter(
  cloudEnabled: boolean,
  onFetchError: (remoteId: string) => void
): RemoteThreadListAdapter {
  return {
    async list(params) {
      if (!cloudEnabled) return { threads: [] }
      const page = await agentsApi.listThreadsPage({
        offset: Number(params?.after ?? 0),
        limit: 100,
        all: true,
      })
      return {
        threads: page.items.map(cloudMetadata),
        nextCursor: page.hasMore ? String(page.offset + page.limit) : undefined,
      }
    },
    async fetch(remoteId) {
      try {
        const { transport, id } = parseRuntimeThreadId(remoteId)
        if (transport === "cloud")
          return cloudMetadata(await agentsApi.getThread(id))
        const thread = await window.openSweDesktop?.getLocalThread(id)
        if (!thread) throw new Error("Local thread not found")
        return {
          status: "regular",
          remoteId,
          externalId: id,
          title: thread.title,
        }
      } catch (error) {
        onFetchError(remoteId)
        throw error
      }
    },
    async initialize() {
      // The first run.start creates and authorizes the cloud thread.
      const id = crypto.randomUUID()
      return { remoteId: runtimeThreadId("cloud", id), externalId: id }
    },
    async rename(remoteId, title) {
      const { transport, id } = parseRuntimeThreadId(remoteId)
      if (transport === "cloud") await agentsApi.renameThread(id, title)
      else
        await window.openSweDesktop?.updateLocalThread({ threadId: id, title })
    },
    async delete(remoteId) {
      const { transport, id } = parseRuntimeThreadId(remoteId)
      if (transport === "cloud") await agentsApi.deleteThread(id)
      else await window.openSweDesktop?.deleteLocalThread(id)
    },
    async archive() {
      throw new Error("Use the thread's Resolve action")
    },
    async unarchive() {
      throw new Error("Use the thread's Resolve action")
    },
    async generateTitle() {
      // The backend owns thread titles.
      return new ReadableStream({
        start(controller) {
          controller.close()
        },
      })
    },
  }
}
