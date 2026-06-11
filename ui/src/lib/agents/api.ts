import type { AgentSchedule, AgentThread, ImageChunk, Message } from "./types"

export type { AgentSchedule, AgentThread, Message }

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
}

export interface ScheduleCreateRequest {
  prompt: string
  schedule: string
  name?: string | null
  repo?: string | null
  model_id?: string | null
  effort?: string | null
}

export interface ScheduleUpdateRequest {
  prompt?: string | null
  schedule?: string | null
  name?: string | null
  repo?: string | null
  model_id?: string | null
  effort?: string | null
  enabled?: boolean | null
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

export interface ThreadPrDiff {
  prNumber: number
  baseSha: string
  headSha: string
  truncated: boolean
  files: Array<ThreadPrDiffFile>
}

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(
  /\/$/,
  ""
)

export const agentsLangGraphApiUrl = `${API_BASE}/dashboard/api`

async function agentsRequest<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
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

export const agentsApi = {
  langGraphApiUrl: agentsLangGraphApiUrl,
  listThreads: () => agentsRequest<Array<AgentThread>>("/threads"),
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
  deleteThread: (threadId: string) =>
    agentsRequest<void>(`/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    }),
  getThreadPrDiff: (threadId: string) =>
    agentsRequest<ThreadPrDiff>(
      `/threads/${encodeURIComponent(threadId)}/pr-diff`
    ),
  streamUrl: (threadId: string) =>
    `${API_BASE}/dashboard/api/threads/${encodeURIComponent(threadId)}/stream`,
}

export type ThreadGroup = "today" | "last7" | "last30" | "older"

export function groupThreads(
  threads: Array<AgentThread>
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

  for (const thread of [...threads].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (thread.updatedAt >= todayStart.getTime()) {
      groups.today.push(thread)
    } else if (thread.updatedAt >= sevenDaysAgo) {
      groups.last7.push(thread)
    } else if (thread.updatedAt >= thirtyDaysAgo) {
      groups.last30.push(thread)
    } else {
      groups.older.push(thread)
    }
  }

  return groups
}
