import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffect, useRef } from "react"

import { agentsApi } from "./api"
import type { InfiniteData, QueryClient, QueryKey } from "@tanstack/react-query"
import type {
  ScheduleUpdateRequest,
  ThreadsPage,
  ThreadsPageParams,
} from "./api"
import type {
  AgentSchedule,
  AgentStatus,
  AgentThread,
  Chunk,
  ImageChunk,
  Message,
  ReviewPageRef,
  WorkflowApprovalStatus,
  WorkflowPushApprovalsResponse,
} from "./types"
import { useSidebarPrefsHydrated } from "./sidebarPrefs"
import type { ChatSort } from "./sidebarPrefs"
import type { Skill, SkillInput } from "@/lib/api"
import { api } from "@/lib/api"
import { chatRoutes } from "@/lib/chatRoutes"
import { optimisticUpdate } from "@/lib/optimistic"

export const agentThreadKeys = {
  lists: ["agent-threads", "lists"] as const,
  pinned: ["agent-threads", "lists", "pinned"] as const,
  repos: (params: { includeResolved: boolean; includeAutomations: boolean }) =>
    ["agent-threads", "lists", "repos", params] as const,
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

function patchAgentThread(
  queryClient: QueryClient,
  threadId: string,
  patch: Partial<AgentThread>
): void {
  const update = (thread: AgentThread) =>
    thread.id === threadId ? { ...thread, ...patch } : thread
  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.detail(threadId),
    (prev) => (prev ? update(prev) : prev)
  )
  queryClient.setQueryData<Array<AgentThread>>(agentThreadKeys.pinned, (prev) =>
    prev?.map(update)
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
  queryClient.setQueriesData<ThreadsPage>(
    { queryKey: ["agent-threads", "lists", "page"] },
    (prev) => prev && { ...prev, items: prev.items.map(update) }
  )
  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.sidebarActive(threadId),
    (prev) => (prev ? update(prev) : prev)
  )
}

export function setAgentThreadStatus(
  queryClient: QueryClient,
  threadId: string,
  status: AgentStatus
): void {
  patchAgentThread(queryClient, threadId, { status })
}

export function setAgentThreadTitle(
  queryClient: QueryClient,
  threadId: string,
  title: string
): void {
  patchAgentThread(queryClient, threadId, { title })
}

function findCachedAgentThread(
  queryClient: QueryClient,
  threadId: string
): AgentThread | undefined {
  const matches = (thread: AgentThread) => thread.id === threadId
  return (
    queryClient.getQueryData<AgentThread>(agentThreadKeys.detail(threadId)) ??
    queryClient.getQueryData<AgentThread>(
      agentThreadKeys.sidebarActive(threadId)
    ) ??
    queryClient
      .getQueryData<Array<AgentThread>>(agentThreadKeys.pinned)
      ?.find(matches) ??
    queryClient
      .getQueriesData<InfiniteData<ThreadsPage>>({
        queryKey: ["agent-threads", "lists", "infinite-pages"],
      })
      .flatMap(([, data]) => data?.pages.flatMap((page) => page.items) ?? [])
      .find(matches) ??
    queryClient
      .getQueriesData<ThreadsPage>({
        queryKey: ["agent-threads", "lists", "page"],
      })
      .flatMap(([, data]) => data?.items ?? [])
      .find(matches)
  )
}

function setAgentThreadPinned(
  queryClient: QueryClient,
  threadId: string,
  pinned: boolean
): void {
  const thread = findCachedAgentThread(queryClient, threadId)
  queryClient.setQueryData<Array<AgentThread>>(
    agentThreadKeys.pinned,
    (prev) => {
      if (!prev) return prev
      const isPinned = prev.some((candidate) => candidate.id === threadId)
      if (pinned) return isPinned || !thread ? prev : [thread, ...prev]
      return isPinned
        ? prev.filter((candidate) => candidate.id !== threadId)
        : prev
    }
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

export type AgentThreadQuerySnapshot = [QueryKey, unknown, boolean]

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
      queryKey: agentThreadKeys.pinned,
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

export interface AgentThreadOptimisticUpdate {
  previous: Array<AgentThreadQuerySnapshot>
  optimistic: Map<QueryKey, number | undefined>
}

/** Cancel in-flight reads of a thread's caches, snapshot them, then apply. */
export async function beginAgentThreadUpdate(
  queryClient: QueryClient,
  threadId: string,
  apply: () => void
): Promise<AgentThreadOptimisticUpdate> {
  await Promise.all([
    queryClient.cancelQueries({
      queryKey: agentThreadKeys.detail(threadId),
      exact: true,
    }),
    queryClient.cancelQueries({
      queryKey: agentThreadKeys.sidebarActive(threadId),
      exact: true,
    }),
    queryClient.cancelQueries({
      queryKey: agentThreadKeys.pinned,
      exact: true,
    }),
    queryClient.cancelQueries({
      queryKey: ["agent-threads", "lists", "infinite-pages"],
    }),
    queryClient.cancelQueries({
      queryKey: ["agent-threads", "lists", "page"],
    }),
  ])
  const previous = snapshotAgentThreadQueries(queryClient, threadId)
  apply()
  const optimistic = new Map<QueryKey, number | undefined>(
    previous.map(([key]) => [
      key,
      queryClient.getQueryState(key)?.dataUpdatedAt,
    ])
  )
  return { previous, optimistic }
}

export function restoreAgentThreadQueries(
  queryClient: QueryClient,
  { previous: snapshots, optimistic }: AgentThreadOptimisticUpdate
): void {
  for (const [key, data, existed] of snapshots) {
    if (queryClient.getQueryState(key)?.dataUpdatedAt !== optimistic.get(key)) {
      continue
    }
    if (existed) queryClient.setQueryData(key, data)
    else queryClient.removeQueries({ queryKey: key, exact: true })
  }
}

/**
 * Clear a thread's unread dot the instant its row is clicked. The detail GET
 * that navigation triggers is what actually marks the thread viewed
 * server-side; this only stops the dot from lingering for that round trip.
 */
export function markAgentThreadViewed(
  queryClient: QueryClient,
  threadId: string
): void {
  const view = (thread: AgentThread) =>
    thread.id === threadId && !thread.viewed
      ? { ...thread, viewed: true, viewedAt: Date.now() }
      : thread
  const viewList = (threads: Array<AgentThread>) => threads.map(view)

  // Patched as already-stale: the detail GET is what marks the thread viewed
  // server-side, and a plain setQueryData would stamp this fresh under the
  // detail query's staleTime — suppressing that fetch, so the next list refetch
  // would serve `viewed: false` right back and the dot would return.
  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.detail(threadId),
    (prev) => (prev ? view(prev) : prev),
    { updatedAt: 0 }
  )
  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.sidebarActive(threadId),
    (prev) => (prev ? view(prev) : prev)
  )
  queryClient.setQueryData<Array<AgentThread>>(
    agentThreadKeys.pinned,
    (prev) => (prev ? viewList(prev) : prev)
  )
  queryClient.setQueriesData<InfiniteData<ThreadsPage>>(
    { queryKey: ["agent-threads", "lists", "infinite-pages"] },
    (prev) =>
      prev && {
        ...prev,
        pages: prev.pages.map((page) => ({
          ...page,
          items: viewList(page.items),
        })),
      }
  )
  queryClient.setQueriesData<ThreadsPage>(
    { queryKey: ["agent-threads", "lists", "page"] },
    (prev) => prev && { ...prev, items: viewList(prev.items) }
  )
}

/** A review has no thread detail GET to mark it read, so it posts its own. */
export function markReviewViewed(
  queryClient: QueryClient,
  review: ReviewPageRef,
  threadId?: string
): void {
  if (threadId) markAgentThreadViewed(queryClient, threadId)
  api
    .markReviewViewed(review.owner, review.repo, review.number)
    .catch((error: unknown) =>
      console.warn("Could not mark the review read", { review, error })
    )
}

export function reviewChatQuery(review: ReviewPageRef) {
  return {
    queryKey: ["review-chat", review.owner, review.repo, review.number],
    queryFn: () => api.getReviewChat(review.owner, review.repo, review.number),
  } as const
}

export function setAgentThreadResolved(
  queryClient: QueryClient,
  threadId: string,
  resolved: boolean
): void {
  const update = (thread: AgentThread) =>
    thread.id === threadId ? { ...thread, resolved } : thread
  const cachedThread = findCachedAgentThread(queryClient, threadId)

  queryClient.setQueryData<AgentThread>(
    agentThreadKeys.detail(threadId),
    (prev) => (prev ? update(prev) : prev)
  )
  queryClient.setQueryData<Array<AgentThread>>(agentThreadKeys.pinned, (prev) =>
    prev?.map(update)
  )
  for (const [key] of queryClient.getQueriesData<InfiniteData<ThreadsPage>>({
    queryKey: ["agent-threads", "lists", "infinite-pages"],
  })) {
    const params = key[3] as Omit<ThreadsPageParams, "offset">
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
  for (const [key] of queryClient.getQueriesData<ThreadsPage>({
    queryKey: ["agent-threads", "lists", "page"],
  })) {
    const params = key[3] as ThreadsPageParams
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
  queryClient.setQueryData(agentThreadKeys.sidebarActive(thread.id), thread)
}

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

export const agentMutationKeys = {
  pin: ["agent-threads", "pin"] as const,
  resolve: ["agent-threads", "resolve"] as const,
  updateSchedule: ["agent-schedules", "update"] as const,
  workflowDecision: (threadId: string) =>
    ["workflow-approvals", threadId, "decision"] as const,
}
const pinMutationKey = agentMutationKeys.pin

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

export const workspaceOptionKeys = {
  all: ["workspace-options"] as const,
}

/** Workspaces a new thread can boot from. Empty when none are configured. */
export function useWorkspaceOptions(enabled = true) {
  return useQuery({
    queryKey: workspaceOptionKeys.all,
    queryFn: api.listWorkspaceOptions,
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
    meta: { silent: true },
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
    meta: { errorTitle: "Couldn't delete skill" },
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

function sidebarPageParams({
  includeAutomations,
  includeResolved,
}: {
  includeAutomations: boolean
  includeResolved: boolean
}): Omit<ThreadsPageParams, "offset"> {
  return {
    limit: SIDEBAR_PAGE_SIZE,
    ...(includeResolved ? {} : { resolved: false }),
    scope: includeAutomations ? "all" : "interactive",
  }
}

export function useSidebarPinnedThreads({ enabled = true } = {}) {
  return useQuery({
    queryKey: agentThreadKeys.pinned,
    queryFn: agentsApi.listPinnedThreads,
    enabled,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
    refetchInterval: (query) =>
      query.state.data?.some((thread) => thread.status === "running")
        ? 2000
        : false,
  })
}

export function useSidebarRepos({
  includeAutomations = false,
  includeResolved = false,
  enabled = true,
}: {
  includeAutomations?: boolean
  includeResolved?: boolean
  enabled?: boolean
}) {
  const params = { includeAutomations, includeResolved }
  const hydrated = useSidebarPrefsHydrated()
  return useQuery({
    queryKey: agentThreadKeys.repos(params),
    queryFn: () => agentsApi.listThreadRepos(params),
    enabled: enabled && hydrated,
    placeholderData: (previous) => previous,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  })
}

export function useSidebarActiveThread({
  activeThreadId,
  loadedThreads,
  includeResolved = false,
  enabled = true,
}: {
  activeThreadId?: string
  loadedThreads: Array<AgentThread>
  includeResolved?: boolean
  enabled?: boolean
}): AgentThread | undefined {
  const loaded = loadedThreads.some((thread) => thread.id === activeThreadId)
  const query = useQuery({
    queryKey: agentThreadKeys.sidebarActive(activeThreadId ?? ""),
    queryFn: () => agentsApi.getThread(activeThreadId!, { markViewed: false }),
    enabled: enabled && Boolean(activeThreadId) && !loaded,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
    refetchInterval: (current) =>
      current.state.data?.status === "running" ? 2000 : false,
    retry: false,
  })
  return !loaded && (!query.data?.resolved || includeResolved)
    ? query.data
    : undefined
}

function useSidebarThreadPages(
  params: Omit<ThreadsPageParams, "offset">,
  enabled: boolean
) {
  const hydrated = useSidebarPrefsHydrated()
  const query = useInfiniteThreadsPages(params, {
    enabled: enabled && hydrated,
    pollWhileRunning: true,
  })
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    hasMore: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    fetchNextPage: () => void query.fetchNextPage(),
  }
}

/** Exported so the head-script warmup can be tested against the real request. */
export function sidebarRecentsParams({
  repoMode,
  includeAutomations = false,
  includeResolved = false,
  sort = "created",
}: {
  repoMode: boolean
  includeAutomations?: boolean
  includeResolved?: boolean
  sort?: ChatSort
}): Omit<ThreadsPageParams, "offset"> {
  return {
    ...sidebarPageParams({ includeAutomations, includeResolved }),
    ...(repoMode ? { ownerless: true } : {}),
    sortBy: sort === "created" ? "created_at" : "updated_at",
  }
}

export function useSidebarRecents({
  repoMode,
  includeAutomations = false,
  includeResolved = false,
  sort = "created",
  enabled = true,
}: {
  repoMode: boolean
  includeAutomations?: boolean
  includeResolved?: boolean
  sort?: ChatSort
  enabled?: boolean
}) {
  return useSidebarThreadPages(
    sidebarRecentsParams({
      repoMode,
      includeAutomations,
      includeResolved,
      sort,
    }),
    enabled
  )
}

export function useSidebarRepoThreads({
  repoFullName,
  includeAutomations = false,
  includeResolved = false,
  sort = "created",
  enabled = true,
}: {
  repoFullName: string | null
  includeAutomations?: boolean
  includeResolved?: boolean
  sort?: ChatSort
  enabled?: boolean
}) {
  return useSidebarThreadPages(
    {
      ...sidebarPageParams({ includeAutomations, includeResolved }),
      ...(repoFullName ? { repo: repoFullName } : {}),
      sortBy: sort === "created" ? "created_at" : "updated_at",
    },
    enabled && Boolean(repoFullName)
  )
}

export function useAgentThread(threadId: string) {
  const queryClient = useQueryClient()
  const queryKey = agentThreadKeys.detail(threadId)

  return useQuery({
    queryKey,
    queryFn: async ({ queryKey: key }) => {
      const thread = await agentsApi.getThread(threadId)
      const cached = queryClient.getQueryData<AgentThread>(key)
      const pendingMessages = cached?.pendingMessages
      if (!pendingMessages?.length) return thread
      return { ...thread, pendingMessages }
    },
    // Server truth heartbeat while a run is live. The SDK's SSE transport does
    // not reconnect once a custom `fetch` is supplied (it needs the dashboard
    // session cookie), so a dropped event stream must not leave the view — and
    // its stop button — believing the run already ended.
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 3000 : false,
    // Lets the optimistic detail seeded by `AgentsHome` survive until the
    // proxied run.start stamps the server-side thread; an immediate refetch
    // would 404 and replace the seeded view with a load error.
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
    mutationKey: agentMutationKeys.workflowDecision(threadId),
    mutationFn: (vars: {
      fingerprint: string
      decision: "approve" | "reject"
    }) =>
      vars.decision === "approve"
        ? agentsApi.approveWorkflowPush(threadId, vars.fingerprint)
        : agentsApi.rejectWorkflowPush(threadId, vars.fingerprint),
    meta: { errorTitle: "Couldn't record workflow push decision" },
    onMutate: async (vars) => {
      const status: WorkflowApprovalStatus =
        vars.decision === "approve" ? "approved" : "rejected"
      return {
        undo: await optimisticUpdate<WorkflowPushApprovalsResponse>(
          queryClient,
          agentThreadKeys.workflowApprovals(threadId),
          (prev) => ({
            ...prev,
            approvals: prev.approvals.map((approval) =>
              approval.fingerprint === vars.fingerprint
                ? { ...approval, status }
                : approval
            ),
          })
        ),
      }
    },
    onError: (_error, _vars, context) => context?.undo(),
    onSettled: () => {
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
    meta: { errorTitle: "Couldn't create automation" },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useUpdateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationKey: agentMutationKeys.updateSchedule,
    mutationFn: (vars: { scheduleId: string; body: ScheduleUpdateRequest }) =>
      agentsApi.updateSchedule(vars.scheduleId, vars.body),
    meta: { errorTitle: "Couldn't update automation" },
    onMutate: async (vars) => {
      const { enabled } = vars.body
      if (enabled == null) return undefined
      return {
        undo: await optimisticUpdate<Array<AgentSchedule>>(
          queryClient,
          agentScheduleKeys.all,
          (prev) =>
            prev.map((schedule) =>
              schedule.id === vars.scheduleId
                ? { ...schedule, enabled }
                : schedule
            )
        ),
      }
    },
    onError: (_error, _vars, context) => context?.undo(),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useTriggerAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.triggerSchedule,
    meta: { errorTitle: "Couldn't run automation" },
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
    meta: { errorTitle: "Couldn't delete automation" },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export interface CreateAgentThreadVariables {
  visibility?: "public" | "private"
  prompt: string
  images?: Array<ImageChunk>
  /** Id the run was started with, shared with the graph's HumanMessage. */
  client_message_id?: string
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
  vars: CreateAgentThreadVariables,
  options: {
    /**
     * Whether the server records new threads into the transcript log. The seed
     * has to carry the same `transcript` stamp the server writes, or the thread
     * page picks the SDK stream first and swaps sources on the next refetch.
     */
    recorded?: boolean
  } = {}
): AgentThread {
  const now = Date.now()
  const text = vars.prompt.trim()
  const repoFullName = vars.repo ?? ""
  const chunks: Array<Chunk> = [
    ...(vars.images ?? []),
    ...(text ? [{ kind: "text", text } satisfies Chunk] : []),
  ]
  const message: Message = {
    id: vars.client_message_id ?? `optimistic-user-${threadId}`,
    author: "user",
    timestamp: new Date(now).toISOString(),
    chunks,
  }
  return {
    id: threadId,
    visibility: vars.visibility ?? "public",
    ...(options.recorded ? { transcript: "v2" as const } : {}),
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
    messages: chunks.length > 0 ? [message] : [],
    // The thread page reads the transcript from its own source, which starts
    // empty while it hydrates. Carrying the prompt as a pending message keeps
    // it on screen across the handoff; the source drops it again as soon as it
    // has the message under the same id.
    pendingMessages:
      chunks.length > 0
        ? [
            {
              id: message.id,
              content: text,
              images: vars.images,
              createdAt: now,
              status: "sending",
            },
          ]
        : [],
  }
}

export interface SendAgentMessageVariables {
  content: string
  images?: Array<ImageChunk>
  model_id?: string | null
  effort?: string | null
  client_message_id?: string
  /** Queue behind the live run instead of steering it. */
  enqueue?: boolean
}

export function useCancelAgentThread(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => agentsApi.cancelThread(threadId),
    meta: { errorTitle: "Couldn't stop the run" },
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
    meta: { errorTitle: "Couldn't cancel thread" },
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
    meta: { errorTitle: "Couldn't delete thread" },
    onSuccess: (_, threadId) => {
      queryClient.removeQueries({ queryKey: agentThreadKeys.detail(threadId) })
      invalidateAgentThreadLists(queryClient)
      const path = window.location.pathname
      const chat = chatRoutes(path)
      if (path === `${chat.home}/${threadId}`) {
        navigate({ to: chat.home })
      }
    },
  })
}

export function useContinueThreadPrivately() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: (threadId: string) =>
      agentsApi.continueThreadPrivately(threadId),
    meta: { errorTitle: "Couldn't continue thread privately" },
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
      invalidateAgentThreadLists(queryClient)
      navigate({ to: "/agents/$threadId", params: { threadId: thread.id } })
    },
  })
}

export function storeAgentThread(
  queryClient: QueryClient,
  thread: AgentThread
): void {
  queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
  queryClient.setQueryData(agentThreadKeys.sidebarActive(thread.id), thread)
}

export function usePinAgentThread() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationKey: pinMutationKey,
    mutationFn: (vars: { threadId: string; pinned: boolean }) =>
      agentsApi.pinThread(vars.threadId, vars.pinned),
    meta: { errorTitle: "Couldn't pin or unpin thread" },
    onMutate: (vars) =>
      beginAgentThreadUpdate(queryClient, vars.threadId, () =>
        setAgentThreadPinned(queryClient, vars.threadId, vars.pinned)
      ),
    onError: (_error, _vars, context) => {
      if (context) restoreAgentThreadQueries(queryClient, context)
    },
    onSettled: () => {
      // A refetch while another pin is in flight would drop its optimistic row.
      if (queryClient.isMutating({ mutationKey: pinMutationKey }) === 1) {
        invalidateAgentThreadLists(queryClient)
      }
    },
  })
}

export function useRenameAgentThread() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: { threadId: string; title: string }) =>
      agentsApi.renameThread(vars.threadId, vars.title),
    meta: { errorTitle: "Couldn't rename thread" },
    onMutate: (vars) =>
      beginAgentThreadUpdate(queryClient, vars.threadId, () =>
        setAgentThreadTitle(queryClient, vars.threadId, vars.title)
      ),
    onError: (_error, _vars, context) => {
      if (context) restoreAgentThreadQueries(queryClient, context)
    },
    onSuccess: (thread) => storeAgentThread(queryClient, thread),
    onSettled: () => invalidateAgentThreadLists(queryClient),
  })
}

export function useResolveAgentThread() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationKey: agentMutationKeys.resolve,
    mutationFn: (vars: { threadId: string; resolved: boolean }) =>
      agentsApi.resolveThread(vars.threadId, vars.resolved),
    meta: { errorTitle: "Couldn't archive or restore thread" },
    onMutate: (vars) =>
      beginAgentThreadUpdate(queryClient, vars.threadId, () =>
        setAgentThreadResolved(queryClient, vars.threadId, vars.resolved)
      ),
    onError: (_error, _vars, context) => {
      if (context) restoreAgentThreadQueries(queryClient, context)
    },
    onSuccess: (thread) => storeAgentThread(queryClient, thread),
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
