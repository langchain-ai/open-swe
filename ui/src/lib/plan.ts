/**
 * Client for the plan-review API — plain HTTP, no realtime transport.
 *
 * The agent publishes the plan markdown; reviewers read it and leave
 * whole-document comments. On approve/reject the server reads those comments and
 * hands them to the agent as the instruction for the follow-up run.
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

export interface PlanComment {
  id: string
  author: string
  author_login: string
  body: string
  created_at: string
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

export async function getPlanComments(
  threadId: string
): Promise<Array<PlanComment>> {
  const { comments } = await req<{ comments: Array<PlanComment> }>(
    `/plan/${encodeURIComponent(threadId)}/comments`
  )
  return comments
}

export function addPlanComment(
  threadId: string,
  body: string
): Promise<PlanComment> {
  return req(`/plan/${encodeURIComponent(threadId)}/comments`, {
    method: "POST",
    body: JSON.stringify({ body }),
  })
}

export function deletePlanComment(
  threadId: string,
  commentId: string
): Promise<{ ok: boolean }> {
  return req(
    `/plan/${encodeURIComponent(threadId)}/comments/${encodeURIComponent(commentId)}`,
    { method: "DELETE" }
  )
}

export function approvePlan(threadId: string): Promise<{ status: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}/approve`, {
    method: "POST",
  })
}

export function rejectPlan(threadId: string): Promise<{ status: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}/reject`, {
    method: "POST",
  })
}
