import { ApiError } from "@/lib/api"
import {
  REQUEST_ID_HEADER,
  dashboardApiUrl,
  dashboardForwardedHeaders,
  networkError,
  newRequestId,
} from "@/lib/dashboard-fetch"

export type Timestamp = string | number

export interface IncidentPolicy {
  enabled: boolean
  workspace_id: string
  slack_app_id: string
  channel_prefix: string
  excluded_channel_ids: string[]
  model: string | null
  version: number
  enabled_at: number
}

export interface IncidentSettingsPayload {
  policy: IncidentPolicy
  connection: {
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
  const requestId = newRequestId()
  const response = await fetch(dashboardApiUrl(`/incidents${path}`), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      [REQUEST_ID_HEADER]: requestId,
      ...dashboardForwardedHeaders(),
    },
  }).catch((cause: unknown) => {
    throw networkError(cause, requestId)
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(
      response.status,
      typeof body?.detail === "string"
        ? body.detail
        : `Request failed (${response.status}). Please try again.`,
      requestId
    )
  }
  return response.json() as Promise<T>
}

export const incidentsApi = {
  settings: () => request<IncidentSettingsPayload>("/settings"),
  saveSettings: (policy: IncidentPolicy) =>
    request<{ command_id: string; status: string }>("/settings", {
      method: "PATCH",
      body: JSON.stringify({ expected_version: policy.version, policy }),
    }),
}
