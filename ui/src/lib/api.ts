/**
 * Session and workspace lookups: who is signed in, what they can configure at
 * the account level, and the model/repo/environment lists every feature picks
 * from. Feature-specific endpoints live in that feature's own `lib/api.ts`,
 * all built on `lib/apiClient`.
 */

import { dashboardApiHref, request } from "./apiClient"

export interface SessionUser {
  login: string
  email: string | null
  avatar_url: string | null
  is_admin: boolean
  slack_oauth_enabled?: boolean
}

export interface ModelOption {
  id: string
  label: string
  efforts: Array<string>
  default_effort: string
  supports_images: boolean
  context_window?: number | null
}

export interface OptionsPayload {
  models: Array<ModelOption>
  default_agent_model: string
  default_agent_reasoning_effort: string
  default_agent_subagent_model: string
  default_agent_subagent_reasoning_effort: string
}

export interface Profile {
  login?: string
  email?: string
  default_model?: string
  reasoning_effort?: string
  default_subagent_model?: string | null
  subagent_reasoning_effort?: string | null
  default_repo?: string | null
  base_branch?: string | null
  branch_prefix?: string | null
  auto_fix_ci?: boolean
  draft_prs?: boolean
  review_draft_prs?: boolean | null
  updated_at?: string
}

export interface ProfileUpdate {
  default_model: string
  reasoning_effort: string
  default_subagent_model?: string | null
  subagent_reasoning_effort?: string | null
  default_repo?: string | null
  base_branch?: string | null
  branch_prefix?: string | null
  auto_fix_ci?: boolean
  draft_prs?: boolean
  review_draft_prs?: boolean | null
}

export interface Repository {
  full_name: string
  private: boolean
}

export interface Installation {
  id: number
  account: string | null
  account_type: string | null
}

export interface ReposPayload {
  installations: Array<Installation>
  repositories: Array<Repository>
}

/** What a non-admin needs to pick an environment for a new thread. */
export interface EnvironmentOption {
  slug: string
  name: string
  has_snapshot: boolean
}

export interface EnvironmentOptionList {
  environments: Array<EnvironmentOption>
  default_slug: string
}

export const api = {
  me: () => request<SessionUser>("/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  options: () => request<OptionsPayload>("/options"),
  profile: () => request<Profile>("/profile"),
  saveProfile: (body: ProfileUpdate) =>
    request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),
  repos: (options?: { refresh?: boolean }) =>
    request<ReposPayload>(options?.refresh ? "/repos?refresh=true" : "/repos"),
  listEnvironmentOptions: () =>
    request<EnvironmentOptionList>("/environments/options"),
}

export function loginUrl(redirectTo?: string): string {
  const target =
    redirectTo ??
    (typeof window !== "undefined"
      ? `${window.location.pathname}${window.location.search}${window.location.hash}`
      : "")
  const qs = target ? `?redirect_to=${encodeURIComponent(target)}` : ""
  return dashboardApiHref(`/auth/login${qs}`)
}
