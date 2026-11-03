import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { agentsRequest } from "./api"
import type { ThreadsPage } from "./api"
import { useSession } from "@/lib/session"
import { invalidationTopic } from "@/lib/invalidations/topics"
import type { InfiniteData } from "@tanstack/react-query"
import type { AgentThread } from "./types"

export interface ChatProject {
  id: string
  name: string
  repo: string | null
  localFolder: { path: string; deviceId: string } | null
}

export interface ChatProjects {
  projects: ChatProject[]
  memberships: Record<string, string>
}

export interface ChatProjectInput {
  id?: string
  name: string
  repo: string | null
  localFolder: ChatProject["localFolder"]
}

export const chatProjectKeys = {
  all: ["chat-projects"] as const,
  list: (userId: string) => ["chat-projects", "list", userId] as const,
  threads: (userId: string, projectId: string) =>
    ["chat-projects", "threads", userId, projectId] as const,
}

class ChatProjectsClient {
  list() {
    return agentsRequest<ChatProjects>("/chat-projects")
  }

  save({ id, ...input }: ChatProjectInput) {
    return agentsRequest<ChatProject>(
      id ? `/chat-projects/${encodeURIComponent(id)}` : "/chat-projects",
      { method: id ? "PATCH" : "POST", body: JSON.stringify(input) }
    )
  }

  delete(id: string) {
    return agentsRequest<void>(`/chat-projects/${encodeURIComponent(id)}`, {
      method: "DELETE",
    })
  }

  assign({
    threadId,
    projectId,
  }: {
    threadId: string
    projectId: string | null
  }) {
    return agentsRequest<void>(
      `/chat-projects/threads/${encodeURIComponent(threadId)}`,
      { method: "PUT", body: JSON.stringify({ projectId }) }
    )
  }

  threads(projectId: string, offset: number) {
    return agentsRequest<ThreadsPage>(
      `/chat-projects/${encodeURIComponent(projectId)}/threads?limit=20&offset=${offset}`
    )
  }

  deviceId() {
    if (typeof window === "undefined") return ""
    const key = "open-swe.desktop.device-id"
    try {
      let value = window.localStorage.getItem(key)
      if (!value) {
        value = crypto.randomUUID()
        window.localStorage.setItem(key, value)
      }
      return value
    } catch (error) {
      console.warn("Device storage is unavailable", error)
      return ""
    }
  }
}

const client = new ChatProjectsClient()

export const desktopDeviceId = () => client.deviceId()

export function useChatProjects() {
  const { data: session } = useSession()
  return useQuery({
    queryKey: chatProjectKeys.list(session?.user_id ?? ""),
    queryFn: () => client.list(),
    enabled: Boolean(session),
    refetchOnWindowFocus: "always",
    meta: {
      invalidatedBy: [
        invalidationTopic("chat-projects", session?.user_id ?? ""),
      ],
    },
  })
}

export function useSaveChatProject() {
  const qc = useQueryClient()
  const { data: session } = useSession()
  const key = chatProjectKeys.list(session?.user_id ?? "")
  return useMutation({
    mutationFn: (input: ChatProjectInput) => client.save(input),
    meta: { errorTitle: "Couldn't save the project" },
    onMutate: async (input) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<ChatProjects>(key)
      if (input.id) {
        qc.setQueryData<ChatProjects>(key, (current) =>
          current
            ? {
                ...current,
                projects: current.projects.map((project) =>
                  project.id === input.id ? { ...project, ...input } : project
                ),
              }
            : current
        )
      }
      return previous
    },
    onError: (_error, _input, previous) => {
      if (previous) qc.setQueryData(key, previous)
    },
    onSuccess: (saved) => {
      qc.setQueryData<ChatProjects>(key, (current) => ({
        projects: [
          ...(current?.projects ?? []).filter(
            (project) => project.id !== saved.id
          ),
          saved,
        ].sort((left, right) => left.name.localeCompare(right.name)),
        memberships: current?.memberships ?? {},
      }))
    },
    onSettled: () => qc.invalidateQueries({ queryKey: chatProjectKeys.all }),
  })
}

export function useDeleteChatProject() {
  const qc = useQueryClient()
  const { data: session } = useSession()
  const key = chatProjectKeys.list(session?.user_id ?? "")
  return useMutation({
    mutationFn: (id: string) => client.delete(id),
    meta: { errorTitle: "Couldn't remove the project" },
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<ChatProjects>(key)
      qc.setQueryData<ChatProjects>(key, (current) =>
        current
          ? {
              projects: current.projects.filter((project) => project.id !== id),
              memberships: Object.fromEntries(
                Object.entries(current.memberships).filter(
                  ([, project]) => project !== id
                )
              ),
            }
          : current
      )
      return previous
    },
    onError: (_error, _id, previous) => {
      if (previous) qc.setQueryData(key, previous)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: chatProjectKeys.all }),
  })
}

export function useAssignChatProject() {
  const qc = useQueryClient()
  const { data: session } = useSession()
  const key = chatProjectKeys.list(session?.user_id ?? "")
  return useMutation({
    mutationFn: (input: { threadId: string; projectId: string | null }) =>
      client.assign(input),
    scope: { id: "chat-project-membership" },
    meta: { errorTitle: "Couldn't move the chat" },
    onMutate: async ({ threadId, projectId }) => {
      await Promise.all([
        qc.cancelQueries({ queryKey: key }),
        qc.cancelQueries({ queryKey: ["chat-projects", "threads"] }),
      ])
      const previous = qc.getQueryData<ChatProjects>(key)
      const pages = qc.getQueriesData<InfiniteData<ThreadsPage>>({
        queryKey: ["chat-projects", "threads"],
      })
      const cached =
        qc.getQueryData<AgentThread>(["agent-threads", threadId]) ??
        pages
          .flatMap(
            ([, current]) => current?.pages.flatMap((page) => page.items) ?? []
          )
          .find((thread) => thread.id === threadId)
      for (const [queryKey, current] of pages) {
        if (!current) continue
        const destination = queryKey[3] === projectId
        qc.setQueryData<InfiniteData<ThreadsPage>>(queryKey, {
          ...current,
          pages: current.pages.map((page, index) => ({
            ...page,
            items: [
              ...(destination && index === 0 && cached && !cached.resolved
                ? [cached]
                : []),
              ...page.items.filter((thread) => thread.id !== threadId),
            ].sort((left, right) => right.updatedAt - left.updatedAt),
          })),
        })
      }
      qc.setQueryData<ChatProjects>(key, (current) => {
        if (!current) return current
        const memberships = { ...current.memberships }
        if (projectId) memberships[threadId] = projectId
        else delete memberships[threadId]
        return { ...current, memberships }
      })
      return { previous, pages }
    },
    onError: (_error, _input, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous)
      for (const [queryKey, page] of context?.pages ?? [])
        qc.setQueryData(queryKey, page)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: chatProjectKeys.all }),
  })
}

export function useProjectThreads(projectId: string, enabled = true) {
  const { data: session } = useSession()
  return useInfiniteQuery({
    queryKey: chatProjectKeys.threads(session?.user_id ?? "", projectId),
    queryFn: ({ pageParam }) => client.threads(projectId, pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) =>
      page.hasMore ? page.offset + page.items.length : undefined,
    enabled: Boolean(session && projectId && enabled),
    refetchOnWindowFocus: "always",
    meta: {
      invalidatedBy: [
        invalidationTopic("chat-projects", session?.user_id ?? ""),
      ],
    },
  })
}

export function useReorderPinnedChats() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (threadIds: string[]) =>
      agentsRequest<void>("/threads/pins/order", {
        method: "POST",
        body: JSON.stringify({ threadIds }),
      }),
    meta: { errorTitle: "Couldn't reorder pinned chats" },
    onMutate: async (threadIds) => {
      const key = ["agent-threads", "lists", "pinned"]
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<AgentThread[]>(key)
      qc.setQueryData<AgentThread[]>(key, (current) =>
        current
          ? [
              ...threadIds.flatMap(
                (id) => current.find((thread) => thread.id === id) ?? []
              ),
              ...current.filter((thread) => !threadIds.includes(thread.id)),
            ]
          : current
      )
      return previous
    },
    onError: (_error, _variables, previous) => {
      if (previous)
        qc.setQueryData(["agent-threads", "lists", "pinned"], previous)
    },
    onSettled: () =>
      qc.invalidateQueries({ queryKey: ["agent-threads", "lists", "pinned"] }),
  })
}
