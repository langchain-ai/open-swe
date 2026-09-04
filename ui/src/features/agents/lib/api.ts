import type {
  AgentPullRequestContextResponse,
  AgentPullRequestStatusResponse,
  AgentSchedule,
  AgentThread,
  AgentThreadUsage,
  ImageChunk,
  Message,
  SlackNotificationMode,
  WorkflowPushApprovalsResponse,
} from "./types"
import { dashboardApiBase } from "@/lib/api-base"
import {
  dashboardApiUrl,
  dashboardForwardedHeaders,
} from "@/lib/dashboard-fetch"

export type { AgentSchedule, AgentThread, Message, SlackNotificationMode }

export class AgentsApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message)
    this.name = "AgentsApiError"
  }
}

export interface ThreadMessageRequest {
  content: string
  images?: Array<ImageChunk>
  model_id?: string | null
  effort?: string | null
  plan_mode?: boolean
}

export interface ScheduleCreateRequest {
  prompt: string
  schedule: string
  name?: string | null
  repo?: string | null
  slack_channel_id?: string | null
  slack_notification_mode?: SlackNotificationMode
  admin_thread?: boolean
  model_id?: string | null
  effort?: string | null
}

export interface ScheduleUpdateRequest {
  prompt?: string | null
  schedule?: string | null
  name?: string | null
  repo?: string | null
  slack_channel_id?: string | null
  slack_notification_mode?: SlackNotificationMode
  admin_thread?: boolean
  model_id?: string | null
  effort?: string | null
  enabled?: boolean | null
}

export interface ScheduleTriggerResult {
  status: "started"
  schedule_id: string
  thread_id: string
  run_id: string | null
}

export interface ThreadPrDiffFile {
  path: string
  previousPath: string | null
  status: "added" | "removed" | "modified" | "renamed" | string
  additions: number
  deletions: number
  originalContent: string | null
  modifiedContent: string | null
  patch?: string | null
  unrenderable: boolean
}

export interface ThreadBranchDiff {
  prNumber: number | null
  baseRef: string
  headRef: string | null
  baseSha: string
  headSha: string
  truncated: boolean
  files: Array<ThreadPrDiffFile>
}

/** Files changed by a thread run. */
export interface ThreadTurnDiff {
  status: "ready" | "missing" | "error"
  truncated: boolean
  summary: {
    files: number
    additions: number
    deletions: number
  }
  files: Array<ThreadPrDiffFile>
}

export interface ThreadRecoveryPatch {
  blob: Blob
  filename: string
}

export interface CloudTerminalConnection {
  url: string
  protocol: string
  ticket: string
}

export type ThreadScope = "all" | "interactive" | "automation"
export type ThreadSortBy = "created_at" | "updated_at"

export interface ThreadsPageParams {
  limit?: number
  offset?: number
  all?: boolean
  resolved?: boolean
  viewed?: boolean
  source?: string
  status?: string
  q?: string
  scope?: ThreadScope
  automationId?: string
  repo?: string
  ownerless?: boolean
  sortBy?: ThreadSortBy
}

export interface ThreadsPage {
  items: Array<AgentThread>
  total?: number
  limit: number
  offset: number
  hasMore?: boolean
}

export interface SidebarProject {
  repoFullName: string
  name: string
  updatedAt: number
}

const API_BASE = dashboardApiBase()

export const agentsLangGraphApiUrl = `${API_BASE}/dashboard/api`

async function agentsRequest<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const res = await fetch(dashboardApiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...dashboardForwardedHeaders(),
      ...init.headers,
    },
  })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) {
        message =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail)
      }
    } catch {
      /* ignore */
    }
    throw new AgentsApiError(res.status, message)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function filenameFromContentDisposition(value: string | null): string | null {
  const match = /filename="([^"]+)"/.exec(value ?? "")
  return match?.[1] ?? null
}

async function agentsBlobRequest(path: string): Promise<ThreadRecoveryPatch> {
  const res = await fetch(dashboardApiUrl(path), {
    credentials: "include",
    headers: { Accept: "text/x-diff", ...dashboardForwardedHeaders() },
  })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) {
        message =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail)
      }
    } catch {
      /* ignore */
    }
    throw new AgentsApiError(res.status, message)
  }
  return {
    blob: await res.blob(),
    filename:
      filenameFromContentDisposition(res.headers.get("content-disposition")) ??
      "open-swe-recovery.patch",
  }
}

function buildThreadsPageQuery(params: ThreadsPageParams): string {
  const search = new URLSearchParams()
  if (params.limit != null) search.set("limit", String(params.limit))
  if (params.offset != null) search.set("offset", String(params.offset))
  if (params.all != null) search.set("all", String(params.all))
  if (params.resolved != null) search.set("resolved", String(params.resolved))
  if (params.viewed != null) search.set("viewed", String(params.viewed))
  if (params.source) search.set("source", params.source)
  if (params.status) search.set("status", params.status)
  if (params.q) search.set("q", params.q)
  if (params.scope) search.set("scope", params.scope)
  if (params.automationId) search.set("automation_id", params.automationId)
  if (params.repo) search.set("repo", params.repo)
  if (params.ownerless != null)
    search.set("ownerless", String(params.ownerless))
  if (params.sortBy) search.set("sort_by", params.sortBy)
  const query = search.toString()
  return query ? `?${query}` : ""
}

function buildProjectsQuery(params: {
  includeResolved?: boolean
  includeAutomations?: boolean
}): string {
  const search = new URLSearchParams()
  if (params.includeResolved != null)
    search.set("include_resolved", String(params.includeResolved))
  if (params.includeAutomations != null) {
    search.set("include_automations", String(params.includeAutomations))
  }
  const query = search.toString()
  return query ? `?${query}` : ""
}

export type PullRequestCheckState =
  | "failing"
  | "passing"
  | "pending"
  | "unknown"

export type PullRequestLiveState = "open" | "draft" | "merged" | "closed"

/** Live GitHub truth for one PR, keyed `owner/repo#number`. */
export interface PullRequestSnapshot {
  checks: PullRequestCheckState
  state: PullRequestLiveState | null
}

export const agentsApi = {
  langGraphApiUrl: agentsLangGraphApiUrl,
  listThreadProjects: (
    params: {
      includeResolved?: boolean
      includeAutomations?: boolean
    } = {}
  ) =>
    agentsRequest<Array<SidebarProject>>(
      `/threads/projects${buildProjectsQuery(params)}`
    ),
  listPinnedThreads: () => agentsRequest<Array<AgentThread>>("/threads/pinned"),
  listThreadsPage: (params: ThreadsPageParams = {}) =>
    agentsRequest<ThreadsPage>(`/threads/page${buildThreadsPageQuery(params)}`),
  resolveThread: (threadId: string, resolved: boolean) =>
    agentsRequest<AgentThread>(
      `/threads/${encodeURIComponent(threadId)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({ resolved }),
      }
    ),
  pinThread: (threadId: string, pinned: boolean) =>
    agentsRequest<void>(`/threads/${encodeURIComponent(threadId)}/pin`, {
      method: pinned ? "POST" : "DELETE",
    }),
  listSchedules: () => agentsRequest<Array<AgentSchedule>>("/schedules"),
  createSchedule: (body: ScheduleCreateRequest) =>
    agentsRequest<AgentSchedule>("/schedules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateSchedule: (scheduleId: string, body: ScheduleUpdateRequest) =>
    agentsRequest<AgentSchedule>(
      `/schedules/${encodeURIComponent(scheduleId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      }
    ),
  triggerSchedule: (scheduleId: string) =>
    agentsRequest<ScheduleTriggerResult>(
      `/schedules/${encodeURIComponent(scheduleId)}/trigger`,
      { method: "POST" }
    ),
  deleteSchedule: (scheduleId: string) =>
    agentsRequest<void>(`/schedules/${encodeURIComponent(scheduleId)}`, {
      method: "DELETE",
    }),
  getThread: (threadId: string, options?: { markViewed?: boolean }) =>
    agentsRequest<AgentThread>(
      `/threads/${encodeURIComponent(threadId)}${
        options?.markViewed === false ? "?mark_viewed=false" : ""
      }`
    ),
  getThreadUsage: (threadId: string) =>
    agentsRequest<AgentThreadUsage>(
      `/threads/${encodeURIComponent(threadId)}/usage`
    ),
  getPullRequestChecks: (
    pullRequests: Array<{ repoFullName: string; number: number }>
  ) =>
    agentsRequest<Record<string, PullRequestSnapshot>>(
      "/threads/pull-request-checks",
      { method: "POST", body: JSON.stringify({ pullRequests }) }
    ),
  getThreadPullRequestStatus: (threadId: string) =>
    agentsRequest<AgentPullRequestStatusResponse>(
      `/threads/${encodeURIComponent(threadId)}/pull-request-status`
    ),
  getThreadPullRequestContext: (
    threadId: string,
    repoFullName: string,
    number: number
  ) => {
    const query = new URLSearchParams({
      repo_full_name: repoFullName,
      number: String(number),
    })
    return agentsRequest<AgentPullRequestContextResponse>(
      `/threads/${encodeURIComponent(threadId)}/pull-request-context?${query}`
    )
  },
  listWorkflowApprovals: (threadId: string) =>
    agentsRequest<WorkflowPushApprovalsResponse>(
      `/workflow-approval/${encodeURIComponent(threadId)}`
    ),
  approveWorkflowPush: (threadId: string, fingerprint: string) =>
    agentsRequest<{ status: string; fingerprint: string }>(
      `/workflow-approval/${encodeURIComponent(threadId)}/${encodeURIComponent(fingerprint)}/approve`,
      { method: "POST" }
    ),
  rejectWorkflowPush: (threadId: string, fingerprint: string) =>
    agentsRequest<{ status: string; fingerprint: string }>(
      `/workflow-approval/${encodeURIComponent(threadId)}/${encodeURIComponent(fingerprint)}/reject`,
      { method: "POST" }
    ),
  queueMessage: (threadId: string, body: ThreadMessageRequest) =>
    agentsRequest<AgentThread>(
      `/threads/${encodeURIComponent(threadId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),
  cancelThread: (threadId: string) =>
    agentsRequest<AgentThread>(
      `/threads/${encodeURIComponent(threadId)}/cancel`,
      {
        method: "POST",
      }
    ),
  adminCancelThread: (threadId: string) =>
    agentsRequest<AgentThread>(
      `/admin/threads/${encodeURIComponent(threadId)}/cancel`,
      {
        method: "POST",
      }
    ),
  deleteThread: (threadId: string) =>
    agentsRequest<void>(`/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    }),
  getThreadBranchDiff: (threadId: string) =>
    agentsRequest<ThreadBranchDiff>(
      `/threads/${encodeURIComponent(threadId)}/branch-diff`
    ),
  getThreadWorkingTreeDiff: (threadId: string) =>
    agentsRequest<ThreadTurnDiff>(
      `/threads/${encodeURIComponent(threadId)}/working-tree-diff`
    ),
  downloadThreadRecoveryPatch: (threadId: string) =>
    agentsBlobRequest(
      `/threads/${encodeURIComponent(threadId)}/recovery.patch`
    ),
  connectCloudTerminal: (threadId: string) =>
    agentsRequest<CloudTerminalConnection>(
      `/threads/${encodeURIComponent(threadId)}/terminal/connect`,
      { method: "POST" }
    ),
  streamUrl: (threadId: string) =>
    `${API_BASE}/dashboard/api/threads/${encodeURIComponent(threadId)}/stream`,
}

export type ThreadGroup = "today" | "last7" | "last30" | "older"

export function groupThreads(
  threads: Array<AgentThread>,
  timestampField: "createdAt" | "updatedAt" = "updatedAt"
): Record<ThreadGroup, Array<AgentThread>> {
  const todayStart = new Date()
  todayStart.setHours(0, 0, 0, 0)
  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000
  const thirtyDaysAgo = Date.now() - 30 * 24 * 60 * 60 * 1000

  const groups: Record<ThreadGroup, Array<AgentThread>> = {
    today: [],
    last7: [],
    last30: [],
    older: [],
  }

  for (const thread of [...threads].sort(
    (a, b) => b[timestampField] - a[timestampField]
  )) {
    const timestamp = thread[timestampField]
    if (timestamp >= todayStart.getTime()) {
      groups.today.push(thread)
    } else if (timestamp >= sevenDaysAgo) {
      groups.last7.push(thread)
    } else if (timestamp >= thirtyDaysAgo) {
      groups.last30.push(thread)
    } else {
      groups.older.push(thread)
    }
  }

  return groups
}
