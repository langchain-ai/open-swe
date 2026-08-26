/** Client for the sandboxed HTML plan-artifact review API. */

import { dashboardApiBase } from "./api-base"
import {
  dashboardForwardedHeaders,
  dashboardRequestOrigin,
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

export type PlanStatus =
  | "planning"
  | "ready"
  | "shared"
  | "revising"
  | "approved"
  | "cancelled"

export interface PlanApprover {
  id: string
  name: string
  source: string
}

export interface PlanData {
  threadId: string
  status: PlanStatus
  html: string
  markdown: string
  approvedBy: PlanApprover | null
  approvedAt: string | null
  user: PlanUser
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

export function updatePlan(
  threadId: string,
  content: string,
  format: "html" | "markdown"
): Promise<{ status: PlanStatus; html?: string; markdown?: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}`, {
    method: "PUT",
    body: JSON.stringify({ [format]: content }),
  })
}

export function approvePlan(
  threadId: string
): Promise<{ status: string; run_id: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}/approve`, {
    method: "POST",
  })
}

export function rejectPlan(
  threadId: string,
  dispatch = true
): Promise<{ status: string }> {
  return req(`/plan/${encodeURIComponent(threadId)}/reject`, {
    method: "POST",
    body: JSON.stringify({ dispatch }),
  })
}
