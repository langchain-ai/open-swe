import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffect } from "react"

import { agentsApi } from "./api"
import type { QueryClient } from "@tanstack/react-query"
import type {
  ScheduleUpdateRequest,
  SidebarThreads,
  ThreadsPageParams,
} from "./api"
import type { AgentThread, Chunk, ImageChunk, Message } from "./types"

export const agentThreadKeys = {
  lists: ["agent-threads", "lists"] as const,
  sidebar: (params: { activeLimit: number; resolvedLimit: number }) =>
    ["agent-threads", "lists", "sidebar", params] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
  prDiff: (threadId: string) => ["agent-threads", threadId, "pr-diff"] as const,
  workflowApprovals: (threadId: string) =>
    ["agent-threads", threadId, "workflow-approvals"] as const,
  page: (params: ThreadsPageParams) =>
    ["agent-threads", "lists", "page", params] as const,
}

export function invalidateAgentThreadLists(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: agentThreadKeys.lists })
}

export function seedAgentThreadLists(
  queryClient: QueryClient,
  thread: AgentThread
): void {
  queryClient.setQueriesData<SidebarThreads>(
    { queryKey: ["agent-threads", "lists", "sidebar"] },
    (prev) => {
      if (!prev) return prev
      const activeItems = [
        thread,
        ...prev.active.items.filter((item) => item.id !== thread.id),
      ].slice(0, prev.active.limit)
      const resolvedItems = prev.resolved.items.filter(
        (item) => item.id !== thread.id
      )
      return {
        ...prev,
        active: { ...prev.active, items: activeItems },
        resolved: { ...prev.resolved, items: resolvedItems },
      }
    }
  )
}

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

// Sidebar lists and detail reads return the same per-thread summary, so warming
// the detail cache from the already-fetched sidebar avoids a fan-out of one
// request per thread. Navigation stays instant; the real (mark-viewed) fetch
// fires only when a thread is actually opened. The active thread is skipped so
// its live detail query stays the source of truth.
export function useSeedAgentThreadDetails(
  threads: Array<AgentThread>,
  activeThreadId?: string
) {
  const queryClient = useQueryClient()

  useEffect(() => {
    for (const thread of threads) {
      if (thread.id === activeThreadId) continue
      // Seed as already-stale: the detail GET is what marks a thread viewed
      // server-side, so opening a seeded entry must still refetch despite the
      // detail query's `staleTime` (which exists for the optimistic seed).
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread, {
        updatedAt: 0,
      })
    }
  }, [activeThreadId, queryClient, threads])
}

const SIDEBAR_ACTIVE_LIMIT = 50

function sidebarThreads(data?: SidebarThreads): Array<AgentThread> {
  return [...(data?.active.items ?? []), ...(data?.resolved.items ?? [])]
}

function sidebarRefetchInterval(query: { state: { data?: SidebarThreads } }) {
  return sidebarThreads(query.state.data).some(
    (thread) => thread.status === "running"
  )
    ? 2000
    : false
}

export function useSidebarThreads(resolvedLimit: number) {
  const params = {
    activeLimit: SIDEBAR_ACTIVE_LIMIT,
    resolvedLimit,
  }
  return useQuery({
    queryKey: agentThreadKeys.sidebar(params),
    queryFn: () => agentsApi.listSidebarThreads(params),
    refetchInterval: sidebarRefetchInterval,
    placeholderData: (prev) => prev,
  })
}

export function useAgentThread(threadId: string) {
  return useQuery({
    queryKey: agentThreadKeys.detail(threadId),
    queryFn: () => agentsApi.getThread(threadId),
    // Lets the optimistic detail seeded by `AgentsHome` survive until the
    // proxied run.start stamps the server-side thread; an immediate refetch
    // would 404 and bounce the route back to /agents.
    staleTime: 30_000,
  })
}

export function useAgentThreadPrDiff(threadId: string, enabled: boolean) {
  return useQuery({
    queryKey: agentThreadKeys.prDiff(threadId),
    queryFn: () => agentsApi.getThreadPrDiff(threadId),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}

export function useWorkflowApprovals(
  threadId: string,
  options: { pollWhileActive?: boolean } = {}
) {
  return useQuery({
    queryKey: agentThreadKeys.workflowApprovals(threadId),
    queryFn: () => agentsApi.listWorkflowApprovals(threadId),
    enabled: Boolean(threadId),
    refetchInterval: (query) =>
      options.pollWhileActive ||
      query.state.data?.approvals.some(
        (approval) => approval.status === "pending"
      )
        ? 3000
        : false,
    retry: false,
  })
}

export function useWorkflowApprovalDecision(threadId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: {
      fingerprint: string
      decision: "approve" | "reject"
    }) =>
      vars.decision === "approve"
        ? agentsApi.approveWorkflowPush(threadId, vars.fingerprint)
        : agentsApi.rejectWorkflowPush(threadId, vars.fingerprint),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.workflowApprovals(threadId),
      })
      void queryClient.invalidateQueries({
        queryKey: agentThreadKeys.detail(threadId),
      })
      invalidateAgentThreadLists(queryClient)
    },
  })
}

export function useAgentSchedules() {
  return useQuery({
    queryKey: agentScheduleKeys.all,
    queryFn: () => agentsApi.listSchedules(),
  })
}

export function useCreateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.createSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useUpdateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: { scheduleId: string; body: ScheduleUpdateRequest }) =>
      agentsApi.updateSchedule(vars.scheduleId, vars.body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useDeleteAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.deleteSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export interface CreateAgentThreadVariables {
  prompt: string
  images?: Array<ImageChunk>
  repo?: string | null
  repo_explicitly_none?: boolean
  model_id?: string | null
  effort?: string | null
}

/**
 * Build the placeholder thread shown the instant a run is started from the
 * home page — before the server has stamped the thread record. Seeded into
 * the detail + list caches by `AgentsHome` so the `$threadId` route renders
 * immediately (the 30s `staleTime` keeps it from refetching into a 404), then
 * reconciled to server truth by the list's running refetch + the stream's
 * `onCreated` / `onCompleted` invalidations.
 */
export function optimisticThread(
  threadId: string,
  vars: CreateAgentThreadVariables
): AgentThread {
  const now = Date.now()
  const text = vars.prompt.trim()
  const repoFullName = vars.repo ?? ""
  const chunks: Array<Chunk> = [
    ...(vars.images ?? []),
    ...(text ? [{ kind: "text", text } satisfies Chunk] : []),
  ]
  const message: Message = {
    id: `optimistic-user-${threadId}`,
    author: "user",
    timestamp: new Date(now).toISOString(),
    chunks,
  }
  return {
    id: threadId,
    title: text.slice(0, 80) || "New agent",
    repo: repoFullName.split("/")[1] ?? "",
    repoFullName,
    branch: "main",
    model: vars.model_id ?? "Default",
    effort: vars.effort ?? null,
    source: "dashboard",
    status: "running",
    viewed: true,
    viewedAt: now,
    isOwner: true,
    createdAt: now,
    updatedAt: now,
    traceUrl: null,
    sandboxId: null,
    messages: message.chunks.length > 0 ? [message] : [],
  }
}

export interface SendAgentMessageVariables {
  content: string
  images?: Array<ImageChunk>
  model_id?: string | null
  effort?: string | null
  plan_mode?: boolean
}

export function useCancelAgentThread(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => agentsApi.cancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      invalidateAgentThreadLists(queryClient)
    },
  })
}

export function useDeleteAgentThread() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.deleteThread(threadId),
    onSuccess: (_, threadId) => {
      queryClient.removeQueries({ queryKey: agentThreadKeys.detail(threadId) })
      invalidateAgentThreadLists(queryClient)
      const path = window.location.pathname
      if (path.includes(`/agents/${threadId}`)) {
        navigate({ to: "/agents" })
      }
    },
  })
}

export function useResolveAgentThread() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: { threadId: string; resolved: boolean }) =>
      agentsApi.resolveThread(vars.threadId, vars.resolved),
    onSuccess: (thread, vars) => {
      queryClient.setQueryData(agentThreadKeys.detail(vars.threadId), thread)
      invalidateAgentThreadLists(queryClient)
    },
  })
}

export function useThreadsPage(params: ThreadsPageParams) {
  return useQuery({
    queryKey: agentThreadKeys.page(params),
    queryFn: () => agentsApi.listThreadsPage(params),
    placeholderData: (prev) => prev,
  })
}
