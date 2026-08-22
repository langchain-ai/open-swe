import type {
  AgentPullRequestStatusResponse,
  AgentSchedule,
  AgentThread,
  ImageChunk,
  Message,
  SlackNotificationMode,
  WorkflowPushApprovalsResponse,
} from "@/lib/agentTypes"
import { dashboardApiHref, request, requestFile } from "@/lib/apiClient"

export type { AgentSchedule, AgentThread, Message, SlackNotificationMode }

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

export interface ThreadTurnDiffOptions {
  maxFiles?: number
  includeContent?: boolean
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
  sortBy?: ThreadSortBy
}

export interface ThreadsPage {
  items: Array<AgentThread>
  total?: number
  limit: number
  offset: number
  hasMore?: boolean
}

export interface SidebarThreadsGroup {
  items: Array<AgentThread>
  limit: number
  hasMore: boolean
}

export interface SidebarThreads {
  active: SidebarThreadsGroup
  resolved: SidebarThreadsGroup
}

export interface Skill {
  name: string
  description: string
  instructions: string
  created_at?: string
  updated_at?: string
}

export interface SkillInput {
  description: string
  instructions: string
}

export interface SkillsPage {
  items: Array<Skill>
  next_offset: number | null
}

export interface OrganizationSkillsPage {
  items: Array<Skill>
  next_cursor: string | null
}

export const agentsLangGraphApiUrl = dashboardApiHref("")

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
  if (params.sortBy) search.set("sort_by", params.sortBy)
  const query = search.toString()
  return query ? `?${query}` : ""
}

function buildSidebarThreadsQuery(params: {
  activeLimit?: number
  resolvedLimit?: number
  activeThreadId?: string
  includeAutomations?: boolean
}): string {
  const search = new URLSearchParams()
  if (params.activeLimit != null) {
    search.set("active_limit", String(params.activeLimit))
  }
  if (params.resolvedLimit != null) {
    search.set("resolved_limit", String(params.resolvedLimit))
  }
  if (params.activeThreadId) {
    search.set("active_thread_id", params.activeThreadId)
  }
  if (params.includeAutomations != null) {
    search.set("include_automations", String(params.includeAutomations))
  }
  const query = search.toString()
  return query ? `?${query}` : ""
}

export const agentsApi = {
  langGraphApiUrl: agentsLangGraphApiUrl,
  listSidebarThreads: (params: {
    activeLimit?: number
    resolvedLimit?: number
    activeThreadId?: string
    includeAutomations?: boolean
  }) =>
    request<SidebarThreads>(
      `/threads/sidebar${buildSidebarThreadsQuery(params)}`
    ),
  listThreadsPage: (params: ThreadsPageParams = {}) =>
    request<ThreadsPage>(`/threads/page${buildThreadsPageQuery(params)}`),
  resolveThread: (threadId: string, resolved: boolean) =>
    request<AgentThread>(`/threads/${encodeURIComponent(threadId)}/resolve`, {
      method: "POST",
      body: JSON.stringify({ resolved }),
    }),
  listSchedules: () => request<Array<AgentSchedule>>("/schedules"),
  createSchedule: (body: ScheduleCreateRequest) =>
    request<AgentSchedule>("/schedules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateSchedule: (scheduleId: string, body: ScheduleUpdateRequest) =>
    request<AgentSchedule>(`/schedules/${encodeURIComponent(scheduleId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  triggerSchedule: (scheduleId: string) =>
    request<ScheduleTriggerResult>(
      `/schedules/${encodeURIComponent(scheduleId)}/trigger`,
      { method: "POST" }
    ),
  deleteSchedule: (scheduleId: string) =>
    request<void>(`/schedules/${encodeURIComponent(scheduleId)}`, {
      method: "DELETE",
    }),
  getThread: (threadId: string, options?: { markViewed?: boolean }) =>
    request<AgentThread>(
      `/threads/${encodeURIComponent(threadId)}${
        options?.markViewed === false ? "?mark_viewed=false" : ""
      }`
    ),
  getThreadPullRequestStatus: (threadId: string) =>
    request<AgentPullRequestStatusResponse>(
      `/threads/${encodeURIComponent(threadId)}/pull-request-status`
    ),
  listWorkflowApprovals: (threadId: string) =>
    request<WorkflowPushApprovalsResponse>(
      `/workflow-approval/${encodeURIComponent(threadId)}`
    ),
  approveWorkflowPush: (threadId: string, fingerprint: string) =>
    request<{ status: string; fingerprint: string }>(
      `/workflow-approval/${encodeURIComponent(threadId)}/${encodeURIComponent(fingerprint)}/approve`,
      { method: "POST" }
    ),
  rejectWorkflowPush: (threadId: string, fingerprint: string) =>
    request<{ status: string; fingerprint: string }>(
      `/workflow-approval/${encodeURIComponent(threadId)}/${encodeURIComponent(fingerprint)}/reject`,
      { method: "POST" }
    ),
  queueMessage: (threadId: string, body: ThreadMessageRequest) =>
    request<AgentThread>(`/threads/${encodeURIComponent(threadId)}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelThread: (threadId: string) =>
    request<AgentThread>(`/threads/${encodeURIComponent(threadId)}/cancel`, {
      method: "POST",
    }),
  adminCancelThread: (threadId: string) =>
    request<AgentThread>(
      `/admin/threads/${encodeURIComponent(threadId)}/cancel`,
      { method: "POST" }
    ),
  deleteThread: (threadId: string) =>
    request<void>(`/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    }),
  getThreadBranchDiff: (threadId: string) =>
    request<ThreadBranchDiff>(
      `/threads/${encodeURIComponent(threadId)}/branch-diff`
    ),
  getThreadWorkingTreeDiff: (threadId: string) =>
    request<ThreadTurnDiff>(
      `/threads/${encodeURIComponent(threadId)}/working-tree-diff`
    ),
  getThreadRunDiff: (
    threadId: string,
    turnKey: string,
    options: ThreadTurnDiffOptions = {}
  ) => {
    const params = new URLSearchParams({ turn_key: turnKey })
    if (options.maxFiles != null) {
      params.set("max_files", String(options.maxFiles))
    }
    if (options.includeContent != null) {
      params.set("include_content", String(options.includeContent))
    }
    return request<ThreadTurnDiff>(
      `/threads/${encodeURIComponent(threadId)}/run-diff?${params.toString()}`
    )
  },
  downloadThreadRecoveryPatch: (threadId: string) =>
    requestFile(`/threads/${encodeURIComponent(threadId)}/recovery.patch`, {
      accept: "text/x-diff",
      fallbackFilename: "open-swe-recovery.patch",
    }),
  connectCloudTerminal: (threadId: string) =>
    request<CloudTerminalConnection>(
      `/threads/${encodeURIComponent(threadId)}/terminal/connect`,
      { method: "POST" }
    ),
  streamUrl: (threadId: string) =>
    dashboardApiHref(`/threads/${encodeURIComponent(threadId)}/stream`),
  transcribeAudio: async (audio: Blob) => {
    const response = await request<{ text: string }>("/voice/transcriptions", {
      method: "POST",
      body: audio,
      headers: { "Content-Type": audio.type },
    })
    return response.text
  },
  listSkills: (offset = 0) =>
    request<SkillsPage>(`/skills?limit=100&offset=${offset}`),
  createSkill: (name: string, body: SkillInput) =>
    request<Skill>("/skills", {
      method: "POST",
      body: JSON.stringify({ name, ...body }),
    }),
  saveSkill: (name: string, body: SkillInput) =>
    request<Skill>(`/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteSkill: (name: string) =>
    request<void>(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  listOrganizationSkills: (cursor: string | null = null) =>
    request<OrganizationSkillsPage>(
      `/organization-skills?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`
    ),
  createOrganizationSkill: (name: string, body: SkillInput) =>
    request<Skill>("/organization-skills", {
      method: "POST",
      body: JSON.stringify({ name, ...body }),
    }),
  saveOrganizationSkill: (name: string, body: SkillInput) =>
    request<Skill>(`/organization-skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteOrganizationSkill: (name: string) =>
    request<void>(`/organization-skills/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
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
