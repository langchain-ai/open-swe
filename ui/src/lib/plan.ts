/** Client for the sandboxed HTML plan-artifact review API. */

import { dashboardApiBase } from "./api-base"
import {
  DashboardRequestError,
  REQUEST_ID_HEADER,
  dashboardForwardedHeaders,
  dashboardRequestOrigin,
  networkError,
  newRequestId,
} from "./dashboard-fetch"

const API_BASE = dashboardApiBase()

function apiBase(): string {
  if (API_BASE) return API_BASE
  if (typeof window !== "undefined") return window.location.origin
  return dashboardRequestOrigin()
}

export interface PlanUser {
  id: string
  login: string
  email: string | null
  name: string
}

export interface PlanData {
  threadId: string
  status: string
  html: string
  markdown: string
  dismissed: boolean
  user: PlanUser
}

export interface PlanTextAnchor {
  exact: string
  prefix: string
  suffix: string
  context_before?: string
  context_after?: string
  start: number
  end: number
}

export interface PlanComment {
  id: string
  author: string
  author_login: string
  body: string
  created_at: string
  anchor: PlanTextAnchor | null
}

export class PlanApiError extends DashboardRequestError {
  constructor(status: number, message: string, requestId?: string) {
    super(status, message, requestId)
    this.name = "PlanApiError"
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const requestId = newRequestId()
  const res = await fetch(`${apiBase()}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      [REQUEST_ID_HEADER]: requestId,
      ...dashboardForwardedHeaders(),
      ...init.headers,
    },
  }).catch((cause: unknown) => {
    throw networkError(cause, requestId)
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
    throw new PlanApiError(res.status, message, requestId)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export function getPlan(threadId: string): Promise<PlanData> {
  return req<PlanData>(`/plan/${encodeURIComponent(threadId)}`)
}

export function dismissPlan(threadId: string): Promise<{ dismissed: boolean }> {
  return req(`/plan/${encodeURIComponent(threadId)}`, {
    method: "PUT",
    body: JSON.stringify({ dismissed: true }),
  })
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
  body: string,
  anchor: PlanTextAnchor
): Promise<PlanComment> {
  return req(`/plan/${encodeURIComponent(threadId)}/comments`, {
    method: "POST",
    body: JSON.stringify({ body, anchor }),
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
