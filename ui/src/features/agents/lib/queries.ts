import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"

import { agentsApi } from "./api"
import type { InfiniteData, QueryClient, QueryKey } from "@tanstack/react-query"
import type {
  ScheduleUpdateRequest,
  SidebarThreads,
  ThreadsPage,
  ThreadsPageParams,
} from "./api"
import type {
  AgentStatus,
  AgentThread,
  Chunk,
  ImageChunk,
  Message,
} from "./types"
import type { Skill, SkillInput } from "@/lib/api"
import { api } from "@/lib/api"

export const agentThreadKeys = {
  lists: ["agent-threads", "lists"] as const,
  sidebar: (params: {
    activeLimit: number
    resolvedLimit: number
    activeThreadId?: string
    includeAutomations: boolean
  }) => ["agent-threads", "lists", "sidebar", params] as const,
  sidebarActive: (threadId: string) =>
    ["agent-threads", "lists", "sidebar-active", threadId] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
  pullRequestStatus: (threadId: string) =>
    ["agent-threads", threadId, "pull-request-status"] as const,
  branchDiff: (threadId: string) =>
    ["agent-threads", threadId, "branch-diff"] as const,
  workingTreeDiff: (threadId: string) =>
    ["agent-threads", threadId, "working-tree-diff"] as const,
  workflowApprovals: (threadId: string) =>
    ["agent-threads", threadId, "workflow-approvals"] as const,
  page: (params: ThreadsPageParams) =>
    ["agent-threads", "lists", "page", params] as const,
  infinitePages: (params: Omit<ThreadsPageParams, "offset">) =>
    ["agent-threads", "lists", "infinite-pages", params] as const,
}

export function invalidateAgentThreadLists(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: agentThreadKeys.lists })
}

export function setAgentThreadStatus(
  queryClient: QueryClient,
  threadId: string,
  status: AgentStatus
): void {
  const update = (thread: AgentThread) =>
    thread.id === threadId ? { ...thread, status } : thread
  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.detail(threadId),
    (prev) => (prev ? update(prev) : prev)
  )
  queryClient.setQueriesData<SidebarThreads>(
    { queryKey: ["agent-threads", "lists", "sidebar"] },
    (prev) =>
      prev && {
        ...prev,
        active: { ...prev.active, items: prev.active.items.map(update) },
        resolved: { ...prev.resolved, items: prev.resolved.items.map(update) },
      }
  )
  queryClient.setQueriesData<InfiniteData<ThreadsPage>>(
    { queryKey: ["agent-threads", "lists", "infinite-pages"] },
    (prev) =>
      prev && {
        ...prev,
        pages: prev.pages.map((page) => ({
          ...page,
          items: page.items.map(update),
        })),
      }
  )
  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.sidebarActive(threadId),
    (prev) => (prev ? update(prev) : prev)
  )
}

function updateThreadPageResolved(
  page: ThreadsPage,
  params: ThreadsPageParams,
  threadId: string,
  resolved: boolean
): ThreadsPage {
  const thread = page.items.find((item) => item.id === threadId)
  if (!thread) return page
  if (params.resolved != null && params.resolved !== resolved) {
    return {
      ...page,
      items: page.items.filter((item) => item.id !== threadId),
      ...(page.total != null ? { total: Math.max(0, page.total - 1) } : {}),
    }
  }
  return {
    ...page,
    items: page.items.map((item) =>
      item.id === threadId ? { ...item, resolved } : item
    ),
  }
}

type AgentThreadQuerySnapshot = [QueryKey, unknown, boolean]

function snapshotAgentThreadQueries(
  queryClient: QueryClient,
  threadId: string
): Array<AgentThreadQuerySnapshot> {
  const directKeys = [
    agentThreadKeys.detail(threadId),
    agentThreadKeys.sidebarActive(threadId),
  ]
  const direct = directKeys.map((key): AgentThreadQuerySnapshot => {
    const state = queryClient.getQueryState(key)
    return [key, state?.data, Boolean(state)]
  })
  const lists = [
    ...queryClient.getQueriesData({
      queryKey: ["agent-threads", "lists", "sidebar"],
    }),
    ...queryClient.getQueriesData({
      queryKey: ["agent-threads", "lists", "infinite-pages"],
    }),
    ...queryClient.getQueriesData({
      queryKey: ["agent-threads", "lists", "page"],
    }),
  ].map(([key, data]): AgentThreadQuerySnapshot => [key, data, true])
  return [...direct, ...lists]
}

function restoreAgentThreadQueries(
  queryClient: QueryClient,
  snapshots: Array<AgentThreadQuerySnapshot>,
  optimistic: Map<QueryKey, number | undefined>
): void {
  for (const [key, data, existed] of snapshots) {
    if (queryClient.getQueryState(key)?.dataUpdatedAt !== optimistic.get(key)) {
      continue
    }
    if (existed) queryClient.setQueryData(key, data)
    else queryClient.removeQueries({ queryKey: key, exact: true })
  }
}

function setAgentThreadResolved(
  queryClient: QueryClient,
  threadId: string,
  resolved: boolean
): void {
  const update = (thread: AgentThread) =>
    thread.id === threadId ? { ...thread, resolved } : thread
  const detail = queryClient.getQueryData<AgentThread>(
    agentThreadKeys.detail(threadId)
  )
  const sidebarActive = queryClient.getQueryData<AgentThread>(
    agentThreadKeys.sidebarActive(threadId)
  )
  let cachedThread = detail ?? sidebarActive

  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.detail(threadId),
    (prev) => (prev ? update(prev) : prev)
  )
  for (const [key, data] of queryClient.getQueriesData<SidebarThreads>({
    queryKey: ["agent-threads", "lists", "sidebar"],
  })) {
    cachedThread ??= [
      ...(data?.active.items ?? []),
      ...(data?.resolved.items ?? []),
    ].find((thread) => thread.id === threadId)
    queryClient.setQueryData<SidebarThreads>(key, (prev) => {
      if (!prev) return prev
      const move = (source: Array<AgentThread>, target: Array<AgentThread>) => {
        const thread = source.find((item) => item.id === threadId)
        return thread
          ? [update(thread), ...target.filter((item) => item.id !== threadId)]
          : target
      }
      return resolved
        ? {
            ...prev,
            active: {
              ...prev.active,
              items: prev.active.items.filter((item) => item.id !== threadId),
            },
            resolved: {
              ...prev.resolved,
              items: move(prev.active.items, prev.resolved.items).slice(
                0,
                prev.resolved.limit
              ),
            },
          }
        : {
            ...prev,
            active: {
              ...prev.active,
              items: move(prev.resolved.items, prev.active.items).slice(
                0,
                prev.active.limit
              ),
            },
            resolved: {
              ...prev.resolved,
              items: prev.resolved.items.filter((item) => item.id !== threadId),
            },
          }
    })
  }
  for (const [key, data] of queryClient.getQueriesData<
    InfiniteData<ThreadsPage>
  >({ queryKey: ["agent-threads", "lists", "infinite-pages"] })) {
    const params = key[3] as Omit<ThreadsPageParams, "offset">
    cachedThread ??= data?.pages
      .flatMap((page) => page.items)
      .find((thread) => thread.id === threadId)
    queryClient.setQueryData<InfiniteData<ThreadsPage>>(key, (prev) =>
      prev
        ? {
            ...prev,
            pages: prev.pages.map((page) =>
              updateThreadPageResolved(page, params, threadId, resolved)
            ),
          }
        : prev
    )
  }
  for (const [key, data] of queryClient.getQueriesData<ThreadsPage>({
    queryKey: ["agent-threads", "lists", "page"],
  })) {
    const params = key[3] as ThreadsPageParams
    cachedThread ??= data?.items.find((thread) => thread.id === threadId)
    queryClient.setQueryData<ThreadsPage>(key, (prev) =>
      prev ? updateThreadPageResolved(prev, params, threadId, resolved) : prev
    )
  }
  if (cachedThread) {
    queryClient.setQueryData(
      agentThreadKeys.sidebarActive(threadId),
      update(cachedThread)
    )
  }
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
  queryClient.setQueryData(agentThreadKeys.sidebarActive(thread.id), thread)
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

function sidebarThreads(data?: SidebarThreads): Array<AgentThread> {
  return [...(data?.active.items ?? []), ...(data?.resolved.items ?? [])]
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
  const [activeLimit, setActiveLimit] = useState(SIDEBAR_PAGE_SIZE)
  const [resolvedLimit, setResolvedLimit] = useState(SIDEBAR_PAGE_SIZE)
  const params = {
    activeLimit,
    resolvedLimit: includeResolved ? resolvedLimit : 0,
    activeThreadId,
    includeAutomations,
  }
  const query = useQuery({
    queryKey: agentThreadKeys.sidebar(params),
    queryFn: () => agentsApi.listSidebarThreads(params),
    enabled,
    placeholderData: (previous) => previous,
    refetchInterval: (current) =>
      sidebarThreads(current.state.data).some(
        (thread) => thread.status === "running"
      )
        ? 2000
        : false,
  })
  const data = query.data ?? {
    active: { items: [], limit: activeLimit, hasMore: false },
    resolved: { items: [], limit: resolvedLimit, hasMore: false },
  }

  return {
    data,
    activeQuery: {
      isFetchingNextPage:
        query.isFetching && (query.data?.active.limit ?? 0) < activeLimit,
      fetchNextPage: () => setActiveLimit((limit) => limit + SIDEBAR_PAGE_SIZE),
    },
    resolvedQuery: {
      isLoading: includeResolved && query.isPending,
      isFetchingNextPage:
        query.isFetching && (query.data?.resolved.limit ?? 0) < resolvedLimit,
      fetchNextPage: () =>
        setResolvedLimit((limit) => limit + SIDEBAR_PAGE_SIZE),
    },
    isPending: query.isPending,
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
    // Server truth heartbeat while a run is live. The SDK's SSE transport does
    // not reconnect once a custom `fetch` is supplied (it needs the dashboard
    // session cookie), so a dropped event stream must not leave the view — and
    // its stop button — believing the run already ended.
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 3000 : false,
    // Lets the optimistic detail seeded by `AgentsHome` survive until the
    // proxied run.start stamps the server-side thread; an immediate refetch
    // would 404 and bounce the route back to /agents.
    staleTime: 30_000,
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

export function useTriggerAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.triggerSchedule,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
      invalidateAgentThreadLists(queryClient)
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

export function useAdminCancelAgentThread() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.adminCancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
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
    onMutate: async (vars) => {
      await Promise.all([
        queryClient.cancelQueries({
          queryKey: agentThreadKeys.detail(vars.threadId),
          exact: true,
        }),
        queryClient.cancelQueries({
          queryKey: agentThreadKeys.sidebarActive(vars.threadId),
          exact: true,
        }),
        queryClient.cancelQueries({
          queryKey: ["agent-threads", "lists", "infinite-pages"],
        }),
        queryClient.cancelQueries({
          queryKey: ["agent-threads", "lists", "page"],
        }),
      ])
      const previous = snapshotAgentThreadQueries(queryClient, vars.threadId)
      setAgentThreadResolved(queryClient, vars.threadId, vars.resolved)
      const optimistic = new Map<QueryKey, number | undefined>(
        previous.map(([key]) => [
          key,
          queryClient.getQueryState(key)?.dataUpdatedAt,
        ])
      )
      return { previous, optimistic }
    },
    onError: (_error, _vars, context) => {
      if (context) {
        restoreAgentThreadQueries(
          queryClient,
          context.previous,
          context.optimistic
        )
      }
    },
    onSuccess: (thread, vars) => {
      queryClient.setQueryData(agentThreadKeys.detail(vars.threadId), thread)
      queryClient.setQueryData(
        agentThreadKeys.sidebarActive(vars.threadId),
        thread
      )
    },
    onSettled: () => invalidateAgentThreadLists(queryClient),
  })
}

export function useInfiniteThreadsPages(
  params: Omit<ThreadsPageParams, "offset">,
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
    queryFn: ({ pageParam }) =>
      agentsApi.listThreadsPage({ ...params, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (page) =>
      page.hasMore ? page.offset + page.items.length : undefined,
    enabled: options.enabled,
    ...(options.staleWhileRevalidate
      ? {
          staleTime: 30_000,
          gcTime: Infinity,
          refetchOnWindowFocus: true,
        }
      : {}),
  })
  const runningOffsets =
    pagesQuery.data?.pages
      .filter((page) =>
        page.items.some((thread) => thread.status === "running")
      )
      .map((page) => page.offset) ?? []
  const pollOffsets =
    runningOffsets.length > 0 ? [...new Set([0, ...runningOffsets])] : []
  useQuery({
    queryKey: ["agent-thread-page-poll", params, pollOffsets],
    queryFn: async () => {
      const refreshed = await Promise.all(
        pollOffsets.map((offset) =>
          agentsApi.listThreadsPage({ ...params, offset })
        )
      )
      queryClient.setQueryData<InfiniteData<ThreadsPage>>(
        agentThreadKeys.infinitePages(params),
        (current) => {
          if (!current) return current
          const refreshedByOffset = new Map(
            refreshed.map((page) => [page.offset, page])
          )
          const membershipChanged = refreshed.some((page) => {
            const previous = current.pages.find(
              (candidate) => candidate.offset === page.offset
            )
            return (
              !previous ||
              previous.items.map((thread) => thread.id).join("|") !==
                page.items.map((thread) => thread.id).join("|")
            )
          })
          if (membershipChanged) {
            const firstPage = refreshedByOffset.get(0)
            return firstPage
              ? {
                  ...current,
                  pages: [firstPage],
                  pageParams: current.pageParams.slice(0, 1),
                }
              : current
          }
          return {
            ...current,
            pages: current.pages.map(
              (page) => refreshedByOffset.get(page.offset) ?? page
            ),
          }
        }
      )
      return refreshed
    },
    enabled: Boolean(
      options.enabled !== false &&
      options.pollWhileRunning &&
      pollOffsets.length > 0
    ),
    refetchInterval: 2000,
  })
  return pagesQuery
}

export function useThreadsPage(
  params: ThreadsPageParams,
  options: {
    enabled?: boolean
    staleWhileRevalidate?: boolean
    pollWhileRunning?: boolean
  } = {}
) {
  return useQuery({
    queryKey: agentThreadKeys.page(params),
    queryFn: () => agentsApi.listThreadsPage(params),
    enabled: options.enabled,
    placeholderData: (prev) => prev,
    refetchInterval: (query) =>
      options.pollWhileRunning &&
      query.state.data?.items.some((thread) => thread.status === "running")
        ? 2000
        : false,
    ...(options.staleWhileRevalidate
      ? {
          staleTime: 30_000,
          gcTime: Infinity,
          refetchOnWindowFocus: true,
        }
      : {}),
  })
}
