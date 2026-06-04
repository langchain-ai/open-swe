/**
 * Typed client for the open-swe dashboard backend.
 *
 * All requests are sent with credentials so the httpOnly `osw_session`
 * cookie set by the OAuth callback rides along on cross-origin calls.
 */

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(/\/$/, "");

if (!API_BASE && typeof window !== "undefined") {
  console.warn("VITE_DASHBOARD_API_BASE_URL is not set");
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function isGithubReauthError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 401) return true;
  return /github token|re-login required/i.test(error.message);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SessionUser {
  login: string;
  email: string | null;
  avatar_url: string | null;
  is_admin: boolean;
  slack_oauth_enabled?: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  efforts: Array<string>;
  default_effort: string;
}

export interface Profile {
  login?: string;
  email?: string;
  default_model?: string;
  reasoning_effort?: string;
  default_subagent_model?: string | null;
  subagent_reasoning_effort?: string | null;
  default_repo?: string | null;
  base_branch?: string | null;
  branch_prefix?: string | null;
  auto_fix_ci?: boolean;
  create_prs?: boolean;
  review_draft_prs?: boolean | null;
  updated_at?: string;
}

export interface ProfileUpdate {
  default_model: string;
  reasoning_effort: string;
  default_subagent_model?: string | null;
  subagent_reasoning_effort?: string | null;
  default_repo?: string | null;
  base_branch?: string | null;
  branch_prefix?: string | null;
  auto_fix_ci?: boolean;
  create_prs?: boolean;
  review_draft_prs?: boolean | null;
}

export type TriggerMode = "every_push" | "once_per_pr" | "manual";
export type AutofixMode = "off" | "low" | "medium" | "high";

export interface TeamSettings {
  trigger_mode: TriggerMode;
  review_draft_prs: boolean;
  pr_summaries: boolean;
  review_trace_links: boolean;
  autofix_mode: AutofixMode;
  autofix_severity_threshold: AutofixMode;
  default_agent_model?: string | null;
  default_agent_reasoning_effort?: string | null;
  default_agent_subagent_model?: string | null;
  default_agent_subagent_reasoning_effort?: string | null;
  default_reviewer_model?: string | null;
  default_reviewer_reasoning_effort?: string | null;
  default_reviewer_subagent_model?: string | null;
  default_reviewer_subagent_reasoning_effort?: string | null;
  updated_at?: string | null;
}

export interface UserMapping {
  github_login: string;
  work_email: string;
  slack_user_id?: string | null;
  source?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
}

export interface UserMappingUpsert {
  github_login: string;
  work_email: string;
  slack_user_id?: string | null;
}

export interface UserMappingsPage {
  items: Array<UserMapping>;
  total: number;
  page: number;
  page_size: number;
}

export interface Repository {
  full_name: string;
  private: boolean;
}

export interface Installation {
  id: number;
  account: string | null;
  account_type: string | null;
}

export interface ReposPayload {
  installations: Array<Installation>;
  repositories: Array<Repository>;
}

export type ReviewStyleStatus = "idle" | "running" | "completed" | "failed";

export interface ReviewStyle {
  full_name: string;
  owner?: string;
  name?: string;
  status: ReviewStyleStatus;
  custom_prompt: string | null;
  analysis_summary: string | null;
  top_reviewers: Array<string>;
  prs_sampled: number;
  reviews_sampled: number;
  analysis_thread_id: string | null;
  analysis_run_id: string | null;
  error: string | null;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface Skill {
  name: string;
  description: string;
  body: string;
}

export const api = {
  me: () => request<SessionUser>("/me"),
  options: () => request<{ models: Array<ModelOption> }>("/options"),
  profile: () => request<Profile>("/profile"),
  saveProfile: (body: ProfileUpdate) =>
    request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),
  repos: () => request<ReposPayload>("/repos"),
  listReviewStyles: () => request<Array<ReviewStyle>>("/review-styles"),
  createReviewStyle: (full_name: string) =>
    request<ReviewStyle>("/review-styles", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    }),
  getReviewStyle: (full_name: string) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}`),
  saveReviewStylePrompt: (full_name: string, custom_prompt: string) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}`, {
      method: "PUT",
      body: JSON.stringify({ custom_prompt }),
    }),
  analyzeReviewStyle: (full_name: string) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}/analyze`, {
      method: "POST",
    }),
  cancelReviewStyle: (full_name: string) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}/cancel`, {
      method: "POST",
    }),
  deleteReviewStyle: (full_name: string) =>
    request<void>(`/review-styles/${encodeURIComponent(full_name)}`, {
      method: "DELETE",
    }),
  getTeamSettings: () => request<TeamSettings>("/team-settings"),
  saveTeamSettings: (body: TeamSettings) =>
    request<TeamSettings>("/team-settings", { method: "PUT", body: JSON.stringify(body) }),
  listEnabledReviewRepos: () =>
    request<{ repos: Array<string> }>("/enabled-review-repos"),
  setEnabledReviewRepo: (full_name: string, enabled: boolean) =>
    request<{ repos: Array<string> }>("/enabled-review-repos", {
      method: "PUT",
      body: JSON.stringify({ full_name, enabled }),
    }),
  listSkills: () => request<Array<Skill>>("/skills"),
  createSkill: (body: Skill) =>
    request<Skill>("/skills", { method: "POST", body: JSON.stringify(body) }),
  updateSkill: (name: string, body: Skill) =>
    request<Skill>(`/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteSkill: (name: string) =>
    request<void>(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  myMapping: () => request<Partial<UserMapping>>("/my-mapping"),
  adminListUserMappings: (page = 1, pageSize = 20) =>
    request<UserMappingsPage>(
      `/admin/user-mappings?page=${page}&page_size=${pageSize}`,
    ),
  adminSaveUserMapping: (body: UserMappingUpsert) =>
    request<UserMapping>("/admin/user-mappings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  adminDeleteUserMapping: (github_login: string) =>
    request<{ deleted: boolean }>(
      `/admin/user-mappings/${encodeURIComponent(github_login)}`,
      { method: "DELETE" },
    ),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
};

export function loginUrl(redirectTo?: string): string {
  const target = redirectTo ?? (typeof window !== "undefined" ? window.location.origin : "");
  const qs = target ? `?redirect_to=${encodeURIComponent(target)}` : "";
  return `${API_BASE}/dashboard/api/auth/login${qs}`;
}

export function slackConnectUrl(): string {
  return `${API_BASE}/dashboard/api/slack/login`;
}
