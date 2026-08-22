/**
 * Dashboard endpoints behind the settings pages: the signed-in user's custom
 * instructions and third-party connections, plus the per-repo instructions
 * appended to the coding agent's system prompt.
 */

import { dashboardApiHref, request } from "@/lib/apiClient"

export interface UserInstructions {
  login?: string
  instructions: string
  created_at?: string
  updated_at?: string
  updated_by?: string
}

export interface AgentInstructions {
  full_name: string
  owner?: string
  name?: string
  instructions: string
  created_by?: string
  created_at?: string
  updated_at?: string
}

export interface ApiKeyCredentialStatus {
  connected: boolean
  api_key_last4?: string
  updated_at?: string | null
}

export interface ApiKeyConnectBody {
  api_key: string
}

export interface NotionCredentialStatus {
  connected: boolean
  token_expires_at?: string | null
  updated_at?: string | null
}

export interface UserMapping {
  github_login: string
  work_email: string
  slack_user_id?: string | null
  source?: string
  status?: string
  created_at?: string
  updated_at?: string
}

export const settingsApi = {
  getMyInstructions: () => request<UserInstructions>("/me/instructions"),
  saveMyInstructions: (instructions: string) =>
    request<UserInstructions>("/me/instructions", {
      method: "PUT",
      body: JSON.stringify({ instructions }),
    }),
  deleteMyInstructions: () =>
    request<void>("/me/instructions", { method: "DELETE" }),
  listAgentInstructions: () =>
    request<Array<AgentInstructions>>("/agent-instructions"),
  createAgentInstructions: (full_name: string) =>
    request<AgentInstructions>("/agent-instructions", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    }),
  getAgentInstructions: (full_name: string) =>
    request<AgentInstructions>(
      `/agent-instructions/${encodeURIComponent(full_name)}`
    ),
  saveAgentInstructions: (full_name: string, instructions: string) =>
    request<AgentInstructions>(
      `/agent-instructions/${encodeURIComponent(full_name)}`,
      { method: "PUT", body: JSON.stringify({ instructions }) }
    ),
  deleteAgentInstructions: (full_name: string) =>
    request<void>(`/agent-instructions/${encodeURIComponent(full_name)}`, {
      method: "DELETE",
    }),
  myMapping: () => request<Partial<UserMapping>>("/my-mapping"),
  getMyCurrentsStatus: () =>
    request<ApiKeyCredentialStatus>("/my-credentials/currents"),
  connectCurrents: (body: ApiKeyConnectBody) =>
    request<ApiKeyCredentialStatus>("/my-credentials/currents", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  disconnectCurrents: () =>
    request<ApiKeyCredentialStatus>("/my-credentials/currents", {
      method: "DELETE",
    }),
  getMyLangSmithStatus: () =>
    request<ApiKeyCredentialStatus>("/my-credentials/langsmith"),
  connectMyLangSmith: (body: ApiKeyConnectBody) =>
    request<ApiKeyCredentialStatus>("/my-credentials/langsmith", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  disconnectMyLangSmith: () =>
    request<ApiKeyCredentialStatus>("/my-credentials/langsmith", {
      method: "DELETE",
    }),
  getMyNotionStatus: () =>
    request<NotionCredentialStatus>("/my-credentials/notion"),
  disconnectNotion: () =>
    request<NotionCredentialStatus>("/my-credentials/notion", {
      method: "DELETE",
    }),
}

export function slackConnectUrl(): string {
  return dashboardApiHref("/slack/login")
}

export function notionConnectUrl(): string {
  return dashboardApiHref("/notion/login")
}
