/**
 * Client for the plan-review API + the Yjs collaboration endpoint.
 *
 * The plan document and its comment threads sync over a y-websocket connection;
 * approve/reject post the client-harvested comments so the agent receives them
 * as the instruction for the follow-up run.
 */

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(
  /\/$/,
  ""
)

function apiBase(): string {
  if (API_BASE) return API_BASE
  return typeof window !== "undefined" ? window.location.origin : ""
}

export interface PlanUser {
  id: string
  login: string
  email: string | null
  name: string
}

export type PlanStatus =
  | "planning"
  | "ready"
  | "revising"
  | "approved"
  | "cancelled"

export interface PlanData {
  threadId: string
  status: PlanStatus
  markdown: string
  isOwner: boolean
  user: PlanUser
}

export interface HarvestedComment {
  author: string
  body: string
  quote?: string
  resolved: boolean
}

export class PlanApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message)
    this.name = "PlanApiError"
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${apiBase()}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  })
  if (!res.ok) {
    let message = res.statusText
    try {
      const body = await res.json()
      if (body?.detail)
        message =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail)
    } catch {
      /* ignore */
    }
    throw new PlanApiError(res.status, message)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export function getPlan(threadId: string): Promise<PlanData> {
  return req<PlanData>(`/plan/${encodeURIComponent(threadId)}`)
}

export function approvePlan(
  threadId: string,
  comments: Array<HarvestedComment>
): Promise<{ status: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}/approve`, {
    method: "POST",
    body: JSON.stringify({ comments }),
  })
}

export function rejectPlan(
  threadId: string,
  comments: Array<HarvestedComment>
): Promise<{ status: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}/reject`, {
    method: "POST",
    body: JSON.stringify({ comments }),
  })
}

/** Base URL the y-websocket provider connects to; it appends `/<threadId>`. */
export function planCollabUrl(): string {
  const base = apiBase()
  const wsBase = base.replace(/^http/, "ws")
  return `${wsBase}/dashboard/api/plan/yjs`
}
