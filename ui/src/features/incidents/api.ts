import { ApiError } from "@/lib/api"
import {
  dashboardApiUrl,
  dashboardForwardedHeaders,
} from "@/lib/dashboard-fetch"

export type IncidentView = "active" | "inactive" | "all" | "history"
export type IncidentAction =
  | "ask"
  | "investigate_again"
  | "pause"
  | "resume"
  | "complete"
  | "reopen"
export type Timestamp = string | number

export interface IncidentSummary {
  id: string
  channel_id: string
  channel_name: string
  title: string
  is_archived: boolean
  status: string
  reason: string | null
  latest_finding: string | null
  updated_at: Timestamp
  slack_url: string | null
}

export interface IncidentReport {
  id: string
  summary: string
  problem?: string
  previous_occurrence?: string
  impact: string
  cause?: string
  next_steps?: string[]
  outcome: "inconclusive" | "findings"
  hypotheses: Array<{
    title: string
    assessment: "supported" | "plausible" | "rejected"
    evidence_ids: string[]
  }>
  evidence: Array<{
    id: string
    source: string
    url: string
    summary: string
    query?: string
    retrieved_at: Timestamp
  }>
  checked: string[]
  gaps: string[]
  questions: string[]
  created_at: Timestamp
}

export interface IncidentDetailPayload {
  incident: IncidentSummary
  report: IncidentReport | null
  coverage: { gaps: string[] }
  activity: Array<{ id: string; type: string; at: Timestamp; summary: string }>
  allowed_actions: string[]
  trace_url: string | null
}

export interface IncidentPolicy {
  enabled: boolean
  workspace_id: string
  slack_app_id: string
  channel_prefix: string
  excluded_channel_ids: string[]
  model: string | null
  max_model_calls: number
  version: number
  enabled_at: number
}

export interface IncidentSettingsPayload {
  policy: IncidentPolicy
  connection: {
    slack_configured: boolean
    workspace_id: string
    slack_app_id: string
    required_scopes_present?: boolean | null
    verified_at: Timestamp | null
    error: string | null
  }
  last_operation: {
    command_id: string
    status: "pending" | "applied" | "failed"
    error: string | null
  } | null
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(dashboardApiUrl(`/incidents${path}`), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...dashboardForwardedHeaders(),
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(
      response.status,
      typeof body?.detail === "string"
        ? body.detail
        : `Request failed (${response.status}). Please try again.`
    )
  }
  return response.json() as Promise<T>
}

export const incidentsApi = {
  list: (filters: { view: IncidentView; q?: string; cursor?: string }) => {
    const params = new URLSearchParams({ view: filters.view })
    if (filters.q?.trim()) params.set("q", filters.q.trim())
    if (filters.cursor) params.set("cursor", filters.cursor)
    return request<{
      items: IncidentSummary[]
      next_cursor: string | null
    }>(`/records?${params}`)
  },
  detail: (id: string) =>
    request<IncidentDetailPayload>(`/records/${encodeURIComponent(id)}`),
  command: (
    id: string,
    action: IncidentAction,
    requestId: string,
    text?: string
  ) =>
    request<{ command_id: string; status: "accepted" | "duplicate" }>(
      `/records/${encodeURIComponent(id)}/commands`,
      {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId,
          action,
          ...(text ? { text } : {}),
        }),
      }
    ),
  settings: () => request<IncidentSettingsPayload>("/settings"),
  saveSettings: (policy: IncidentPolicy) =>
    request<{ command_id: string; status: string }>("/settings", {
      method: "PATCH",
      body: JSON.stringify({ expected_version: policy.version, policy }),
    }),
}
