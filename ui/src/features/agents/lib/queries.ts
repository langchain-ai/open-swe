import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  useQueries,
} from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useMemo, useRef } from "react"

import { agentsApi } from "./api"
import type { InfiniteData } from "@tanstack/react-query"
import type {
  ScheduleUpdateRequest,
  ThreadsPage,
  ThreadsPageParams,
} from "./api"
import type { AgentThread, Chunk, ImageChunk, Message } from "./types"
import type { Skill, SkillInput } from "@/lib/api"
import { api } from "@/lib/api"

export const agentThreadKeys = {
  lists: ["thread-list"] as const,
  sidebar: (params: {
    activeLimit: number
    resolvedLimit: number
    activeThreadId?: string
    includeAutomations: boolean
  }) => ["thread-list", "sidebar", params] as const,
  sidebarActive: (threadId: string) => ["thread", threadId] as const,
  detail: (threadId: string) => ["thread", threadId] as const,
  messages: (threadId: string) => ["thread-messages", threadId] as const,
  pullRequestStatus: (threadId: string) =>
    ["agent-threads", threadId, "pull-request-status"] as const,
  branchDiff: (threadId: string) =>
    ["agent-threads", threadId, "branch-diff"] as const,
  workingTreeDiff: (threadId: string) =>
    ["agent-threads", threadId, "working-tree-diff"] as const,
  workflowApprovals: (threadId: string) =>
    ["agent-threads", threadId, "workflow-approvals"] as const,
  page: (params: ThreadsPageParams) => ["thread-list", "page", params] as const,
  infinitePages: (params: Omit<ThreadsPageParams, "cursor">) =>
    ["thread-list", "infinite-pages", params] as const,
}

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

export const agentSkillKeys = {
  personal: ["agent-skills", "personal"] as const,
  organization: ["agent-skills", "organization"] as const,
}

const BUNDLED_SKILLS: Array<Skill> = [
  {
    name: "baby-sit",
    description:
      "Monitor a GitHub pull request until CI is green, diagnose failures, and rerun only evidence-backed flaky GitHub Actions jobs.",
    instructions: "",
  },
]

export const environmentOptionKeys = {
  all: ["environment-options"] as const,
}

/** Environments a new thread can boot from. Empty when none are configured. */
export function useEnvironmentOptions(enabled = true) {
  return useQuery({
    queryKey: environmentOptionKeys.all,
    queryFn: api.listEnvironmentOptions,
    staleTime: 60_000,
    enabled,
  })
}

async function listPersonalSkills() {
  const items = []
  let offset = 0
  do {
    const page = await api.listSkills(offset)
    items.push(...page.items)
    offset = page.next_offset ?? 0
  } while (offset)
  return items
}

async function listOrganizationSkills() {
  const items: Array<Skill> = []
  let cursor: string | null = null
  do {
    const page = await api.listOrganizationSkills(cursor)
    items.push(...page.items)
    cursor = page.next_cursor
  } while (cursor)
  return items
}

export function usePersonalAgentSkills(enabled = true) {
  return useQuery({
    queryKey: agentSkillKeys.personal,
    queryFn: listPersonalSkills,
    enabled,
  })
}

export function useOrganizationAgentSkills(enabled = true) {
  return useQuery({
    queryKey: agentSkillKeys.organization,
    queryFn: listOrganizationSkills,
    enabled,
  })
}

export function useAgentSkills(options: { enabled?: boolean } = {}) {
  const enabled = options.enabled ?? true
  const personal = usePersonalAgentSkills(enabled)
  const organization = useOrganizationAgentSkills(enabled)
  return {
    personal: personal.data ?? [],
    organization: organization.data ?? [],
    refetch: async () => {
      const [personalResult, organizationResult] = await Promise.all([
        personal.refetch(),
        organization.refetch(),
      ])
      if (personalResult.error) throw personalResult.error
      if (organizationResult.error) throw organizationResult.error
      return {
        personal: personalResult.data ?? [],
        organization: organizationResult.data ?? [],
      }
    },
    data: [
      ...new Map(
        [
          ...BUNDLED_SKILLS,
          ...(personal.data ?? []),
          ...(organization.data ?? []),
        ].map((skill) => [skill.name, skill])
      ).values(),
    ],
    error: personal.error ?? organization.error,
    isError: personal.isError || organization.isError,
    isLoading: personal.isLoading || organization.isLoading,
  }
}

function useSkillMutation(
  mutationFn: (vars: SkillInput & { name: string }) => Promise<Skill>,
  queryKey: ReadonlyArray<string>
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })
}

export function useCreateAgentSkill(organization = false) {
  return useSkillMutation(
    ({ name, ...body }) =>
      organization
        ? api.createOrganizationSkill(name, body)
        : api.createSkill(name, body),
    organization ? agentSkillKeys.organization : agentSkillKeys.personal
  )
}

export function useUpdateAgentSkill(organization = false) {
  return useSkillMutation(
    ({ name, ...body }) =>
      organization
        ? api.saveOrganizationSkill(name, body)
        : api.saveSkill(name, body),
    organization ? agentSkillKeys.organization : agentSkillKeys.personal
  )
}

export function useDeleteAgentSkill(organization = false) {
  const queryClient = useQueryClient()
  const queryKey = organization
    ? agentSkillKeys.organization
    : agentSkillKeys.personal
  return useMutation({
    mutationFn: organization ? api.deleteOrganizationSkill : api.deleteSkill,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })
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

export const SIDEBAR_PAGE_SIZE = 10

function infinitePageThreads(
  data?: InfiniteData<ThreadsPage>
): Array<AgentThread> {
  const threads = data?.pages.flatMap((page) => page.items) ?? []
  return [...new Map(threads.map((thread) => [thread.id, thread])).values()]
}

type CachedThreadsPage = Omit<ThreadsPage, "items">

function combineThreadEntities(
  results: Array<{ data?: readonly [string, AgentThread] }>
): Map<string, AgentThread> {
  return new Map(
    results.flatMap((result) => (result.data ? [result.data] : []))
  )
}

function useCachedThreadEntities(ids: Array<string>): Map<string, AgentThread> {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: agentThreadKeys.detail(id),
      queryFn: () => agentsApi.getThread(id, { markViewed: false }),
      select: (thread: AgentThread) => [thread.id, thread] as const,
      enabled: false,
    })),
    combine: combineThreadEntities,
  })
}

export function useSidebarThreads({
  activeThreadId,
  includeAutomations = false,
  includeResolved = false,
  enabled = true,
}: {
  activeThreadId?: string
  includeAutomations?: boolean
  includeResolved?: boolean
  enabled?: boolean
}) {
  const scope = includeAutomations ? "all" : "interactive"
  const activeQuery = useInfiniteThreadsPages(
    { limit: SIDEBAR_PAGE_SIZE, resolved: false, scope, sortBy: "created_at" },
    { enabled, pollWhileRunning: true }
  )
  const resolvedQuery = useInfiniteThreadsPages(
    { limit: SIDEBAR_PAGE_SIZE, resolved: true, scope, sortBy: "created_at" },
    { enabled: enabled && includeResolved, pollWhileRunning: true }
  )
  const loadedActive = infinitePageThreads(activeQuery.data)
  const loadedResolved = infinitePageThreads(resolvedQuery.data)
  const activeThreadLoaded = [
    ...loadedActive,
    ...(includeResolved ? loadedResolved : []),
  ].some((thread) => thread.id === activeThreadId)
  const activeThreadQuery = useQuery({
    queryKey: agentThreadKeys.sidebarActive(activeThreadId ?? ""),
    queryFn: () =>
      agentsApi.getThread(activeThreadId as string, { markViewed: false }),
    enabled:
      enabled &&
      Boolean(activeThreadId) &&
      activeQuery.isSuccess &&
      !activeThreadLoaded,
    staleTime: 30_000,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 2000 : false,
    retry: false,
  })
  const pinnedThread = activeThreadLoaded ? undefined : activeThreadQuery.data
  const activeItems =
    pinnedThread && !pinnedThread.resolved
      ? [
          pinnedThread,
          ...loadedActive.filter((thread) => thread.id !== pinnedThread.id),
        ]
      : loadedActive
  const resolvedItems =
    pinnedThread?.resolved && includeResolved
      ? [
          pinnedThread,
          ...loadedResolved.filter((thread) => thread.id !== pinnedThread.id),
        ]
      : loadedResolved

  return {
    data: {
      active: {
        items: activeItems,
        limit: SIDEBAR_PAGE_SIZE,
        hasMore: activeQuery.hasNextPage,
      },
      resolved: {
        items: resolvedItems,
        limit: SIDEBAR_PAGE_SIZE,
        hasMore: resolvedQuery.hasNextPage,
      },
    },
    activeQuery,
    resolvedQuery,
    isPending: activeQuery.isPending,
  }
}

export function useAgentThread(threadId: string) {
  const queryClient = useQueryClient()
  const queryKey = agentThreadKeys.detail(threadId)

  return useQuery({
    queryKey,
    queryFn: async ({ queryKey: key }) => {
      const thread = await agentsApi.getThread(threadId)
      const queuedMessages =
        queryClient.getQueryData<AgentThread>(key)?.queuedMessages
      return thread.status === "running" && queuedMessages?.length
        ? { ...thread, queuedMessages }
        : thread
    },
    // Lets the optimistic detail seeded by `AgentsHome` survive until the
    // proxied run.start stamps the server-side thread; an immediate refetch
    // would 404 and bounce the route back to /agents.
    staleTime: 30_000,
  })
}

export function useAgentThreadMessages(threadId: string) {
  return useQuery({
    queryKey: agentThreadKeys.messages(threadId),
    queryFn: async () => {
      const response = await agentsApi.getThreadMessages(threadId)
      return response.items.map((item) => item.payload)
    },
    enabled: Boolean(threadId),
  })
}

export function useAgentThreadPullRequestStatus(
  threadId: string,
  enabled: boolean
) {
  return useQuery({
    queryKey: agentThreadKeys.pullRequestStatus(threadId),
    queryFn: () => agentsApi.getThreadPullRequestStatus(threadId),
    enabled: enabled && Boolean(threadId),
    staleTime: 15_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: "always",
    retry: false,
  })
}

export function useAgentThreadBranchDiff(threadId: string, enabled: boolean) {
  return useQuery({
    queryKey: agentThreadKeys.branchDiff(threadId),
    queryFn: () => agentsApi.getThreadBranchDiff(threadId),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
}

export function useAgentThreadWorkingTreeDiff(
  threadId: string,
  enabled: boolean,
  pollWhileRunning = false
) {
  const query = useQuery({
    queryKey: agentThreadKeys.workingTreeDiff(threadId),
    queryFn: () => agentsApi.getThreadWorkingTreeDiff(threadId),
    enabled: enabled && Boolean(threadId),
    staleTime: 30_000,
    refetchInterval: pollWhileRunning
      ? 3000
      : (current) => (current.state.data?.status === "ready" ? false : 3000),
    retry: false,
  })

  const { refetch } = query
  const previous = useRef({ enabled: false, pollWhileRunning })
  useEffect(() => {
    const was = previous.current
    previous.current = { enabled, pollWhileRunning }
    if (was.enabled && was.pollWhileRunning && enabled && !pollWhileRunning) {
      const timers = [0, 1000, 3000].map((delay) =>
        window.setTimeout(() => void refetch(), delay)
      )
      return () => timers.forEach(window.clearTimeout)
    }
    if (!was.enabled && enabled && !pollWhileRunning) void refetch()
  }, [enabled, pollWhileRunning, refetch])

  return query
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

export function useTriggerAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.triggerSchedule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
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
    environment: "cloud",
    status: "queued",
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
    },
  })
}

export function useAdminCancelAgentThread() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.adminCancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
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
      agentsApi.patchThread(vars.threadId, { resolved: vars.resolved }),
    onSuccess: (thread, vars) => {
      queryClient.setQueryData(agentThreadKeys.detail(vars.threadId), thread)
    },
  })
}

export function useInfiniteThreadsPages(
  params: Omit<ThreadsPageParams, "cursor">,
  options: {
    enabled?: boolean
    staleWhileRevalidate?: boolean
    pollWhileRunning?: boolean
  } = {}
) {
  const queryClient = useQueryClient()
  const queryKey = agentThreadKeys.infinitePages(params)
  const pagesQuery = useInfiniteQuery({
    queryKey,
    queryFn: async ({ pageParam }) => {
      const page = await agentsApi.listThreadsPage({
        ...params,
        ...(pageParam ? { cursor: pageParam } : {}),
      })
      for (const thread of page.items) {
        queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
      }
      return {
        ids: page.ids,
        limit: page.limit,
        cursor: page.cursor,
        hasMore: page.hasMore,
      } satisfies CachedThreadsPage
    },
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.hasMore && page.cursor ? page.cursor : undefined,
    enabled: options.enabled,
    ...(options.staleWhileRevalidate
      ? {
          staleTime: 30_000,
          gcTime: Infinity,
          refetchOnWindowFocus: true,
        }
      : {}),
  })
  const ids = useMemo(
    () => [
      ...new Set(pagesQuery.data?.pages.flatMap((page) => page.ids) ?? []),
    ],
    [pagesQuery.data?.pages]
  )
  const entities = useCachedThreadEntities(ids)
  const data = useMemo<InfiniteData<ThreadsPage> | undefined>(
    () =>
      pagesQuery.data
        ? {
            pageParams: pagesQuery.data.pageParams,
            pages: pagesQuery.data.pages.map((page) => ({
              ...page,
              items: page.ids.flatMap((id) => {
                const thread = entities.get(id)
                return thread ? [thread] : []
              }),
            })),
          }
        : undefined,
    [entities, pagesQuery.data]
  )
  return { ...pagesQuery, data }
}

export function useThreadsPage(
  params: ThreadsPageParams,
  options: { enabled?: boolean; staleWhileRevalidate?: boolean } = {}
) {
  const queryClient = useQueryClient()
  const pageQuery = useQuery({
    queryKey: agentThreadKeys.page(params),
    queryFn: async () => {
      const page = await agentsApi.listThreadsPage(params)
      for (const thread of page.items) {
        queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
      }
      return {
        ids: page.ids,
        limit: page.limit,
        cursor: page.cursor,
        hasMore: page.hasMore,
      } satisfies CachedThreadsPage
    },
    enabled: options.enabled,
    placeholderData: (prev) => prev,
    ...(options.staleWhileRevalidate
      ? {
          staleTime: 30_000,
          gcTime: Infinity,
          refetchOnWindowFocus: true,
        }
      : {}),
  })
  const ids = pageQuery.data?.ids ?? []
  const entities = useCachedThreadEntities(ids)
  const data = useMemo<ThreadsPage | undefined>(
    () =>
      pageQuery.data
        ? {
            ...pageQuery.data,
            items: ids.flatMap((id) => {
              const thread = entities.get(id)
              return thread ? [thread] : []
            }),
          }
        : undefined,
    [entities, ids, pageQuery.data]
  )
  return { ...pageQuery, data }
}
