/**
 * Workspace administration endpoints: team-wide defaults and credentials, the
 * GitHub-login → work-email directory, sandbox base images and per-repo
 * snapshots, and the reviewer eval runner.
 */

import { request } from "@/lib/apiClient"
import type { UserMapping } from "@/features/settings/lib/api"

export interface TeamSettings {
  review_draft_prs: boolean
  pr_summaries: boolean
  review_trace_links: boolean
  /** Tri-state LLM Gateway toggle; null inherits the LANGSMITH_GATEWAY_ENABLED default. */
  gateway_enabled?: boolean | null
  transcription_model?: string
  fable_enabled?: boolean
  review_tracing_project?: string | null
  org_guidelines?: string | null
  default_agent_model?: string | null
  default_agent_reasoning_effort?: string | null
  default_agent_subagent_model?: string | null
  default_agent_subagent_reasoning_effort?: string | null
  default_repo?: string | null
  default_reviewer_model?: string | null
  default_reviewer_reasoning_effort?: string | null
  default_reviewer_subagent_model?: string | null
  default_reviewer_subagent_reasoning_effort?: string | null
  default_grouping_model?: string | null
  default_grouping_reasoning_effort?: string | null
  default_chat_model?: string | null
  default_chat_reasoning_effort?: string | null
  default_thread_title_model?: string | null
  default_thread_title_reasoning_effort?: string | null
  updated_at?: string | null
}

export interface ProviderCredentialStatus {
  connected: boolean
  site?: string
  endpoint?: string
  api_key_last4?: string
  updated_at?: string | null
}

export interface TeamCredentialsStatus {
  datadog: ProviderCredentialStatus
  langsmith: ProviderCredentialStatus
}

export interface DatadogConnectBody {
  site: string
  api_key: string
  app_key: string
}

export interface LangSmithConnectBody {
  api_key: string
  endpoint?: string | null
}

export interface UserMappingsPage {
  items: Array<UserMapping>
  total: number
  page: number
  page_size: number
}

export interface SandboxSettings {
  base_snapshot_id: string | null
  env_base_snapshot_id: string | null
  effective_base_snapshot_id: string | null
  base_snapshot_source: "admin" | "env" | "unset"
  updated_at: string | null
  updated_by: string | null
}

export type RepoSnapshotStatus = "none" | "building" | "ready" | "failed"

export interface RepoSnapshot {
  full_name: string
  owner?: string
  name?: string
  dockerfile: string
  snapshot_id: string | null
  snapshot_name: string | null
  status: RepoSnapshotStatus
  status_message: string | null
  build_log: string | null
  fs_capacity_bytes?: number
  vcpus?: number
  mem_bytes?: number
  target?: string | null
  build_args?: Record<string, string> | null
  build_started_at?: string | null
  last_built_at?: string | null
  created_by?: string
  created_at?: string
  updated_at?: string
}

export interface RepoSnapshotUpdateBody {
  dockerfile: string
  fs_capacity_bytes?: number | null
  vcpus?: number | null
  mem_bytes?: number | null
  target?: string | null
  build_args?: Record<string, string> | null
}

export type ReviewerEvalScoreMode = "all_findings" | "surfaced_findings"
export type ReviewerEvalSeverity = "low" | "medium" | "high" | "critical"

export interface ReviewerEvalConfig {
  dataset_name: string
  experiment_prefix: string
  max_concurrency: number
  langsmith_project: string
  langgraph_url: string
  assistant_id: string
  model_id: string
  reasoning_effort: string
  score_mode: ReviewerEvalScoreMode
  severity_threshold: ReviewerEvalSeverity
  cap: number
}

export interface ReviewerEvalProgress {
  completed: number
  total: number | null
}

export interface ReviewerEvalStatus {
  name: string
  status: "idle" | "running" | "completed" | "failed"
  run_name?: string
  langsmith_project: string
  limit: number | null
  config_snapshot?: ReviewerEvalConfig
  started_at: string | null
  finished_at: string | null
  created_by: string | null
  pid: number | null
  exit_code: number | null
  experiment_url: string | null
  error: string | null
  log_tail: string | null
  progress?: ReviewerEvalProgress | null
  github_run_url?: string | null
  trigger?: string | null
  updated_at: string
}

export const adminApi = {
  getTeamSettings: () => request<TeamSettings>("/team-settings"),
  saveTeamSettings: (body: TeamSettings) =>
    request<TeamSettings>("/team-settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  saveTranscriptionModel: (transcription_model: string) =>
    request<TeamSettings>("/team-settings/transcription", {
      method: "PUT",
      body: JSON.stringify({ transcription_model }),
    }),
  getTeamCredentials: () => request<TeamCredentialsStatus>("/team-credentials"),
  connectDatadog: (body: DatadogConnectBody) =>
    request<TeamCredentialsStatus>("/team-credentials/datadog", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  disconnectDatadog: () =>
    request<TeamCredentialsStatus>("/team-credentials/datadog", {
      method: "DELETE",
    }),
  connectLangSmith: (body: LangSmithConnectBody) =>
    request<TeamCredentialsStatus>("/team-credentials/langsmith", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  disconnectLangSmith: () =>
    request<TeamCredentialsStatus>("/team-credentials/langsmith", {
      method: "DELETE",
    }),
  listUserMappings: (page = 1, pageSize = 20) =>
    request<UserMappingsPage>(
      `/admin/user-mappings?page=${page}&page_size=${pageSize}`
    ),
  deleteUserMapping: (github_login: string) =>
    request<{ deleted: boolean }>(
      `/admin/user-mappings/${encodeURIComponent(github_login)}`,
      { method: "DELETE" }
    ),
  getSandboxSettings: () => request<SandboxSettings>("/sandbox-settings"),
  saveSandboxSettings: (base_snapshot_id: string | null) =>
    request<SandboxSettings>("/sandbox-settings", {
      method: "PUT",
      body: JSON.stringify({ base_snapshot_id }),
    }),
  listRepoSnapshots: () => request<Array<RepoSnapshot>>("/repo-snapshots"),
  createRepoSnapshot: (full_name: string) =>
    request<RepoSnapshot>("/repo-snapshots", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    }),
  getRepoSnapshot: (full_name: string) =>
    request<RepoSnapshot>(`/repo-snapshots/${encodeURIComponent(full_name)}`),
  getRepoSnapshotTemplate: (full_name: string) =>
    request<{ dockerfile: string }>(
      `/repo-snapshots/template?full_name=${encodeURIComponent(full_name)}`
    ),
  saveRepoSnapshot: (full_name: string, body: RepoSnapshotUpdateBody) =>
    request<RepoSnapshot>(`/repo-snapshots/${encodeURIComponent(full_name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  buildRepoSnapshot: (full_name: string) =>
    request<RepoSnapshot>(
      `/repo-snapshots/${encodeURIComponent(full_name)}/build`,
      { method: "POST" }
    ),
  deleteRepoSnapshot: (full_name: string) =>
    request<void>(`/repo-snapshots/${encodeURIComponent(full_name)}`, {
      method: "DELETE",
    }),
  getReviewerEval: () => request<ReviewerEvalStatus>("/admin/evals/reviewer"),
}
