/**
 * Typed client for the open-swe dashboard backend.
 *
 * All requests are sent with credentials so the httpOnly `osw_session`
 * cookie set by the OAuth callback rides along on cross-origin calls.
 */

const API_BASE = (import.meta.env.VITE_DASHBOARD_API_BASE_URL ?? "").replace(
  /\/$/,
  ""
)

if (!API_BASE && typeof window !== "undefined") {
  console.warn("VITE_DASHBOARD_API_BASE_URL is not set")
}

const GITHUB_IMAGE_HOST_RE =
  /^(?:www\.)?github\.com$|\.githubusercontent\.com$/i

/**
 * Build an authenticated proxy URL for GitHub-hosted PR images. Private-repo
 * attachments can't be loaded directly by the browser, so they're routed
 * through the dashboard backend which holds the App token. Non-GitHub image
 * URLs are returned unchanged.
 */
export function reviewImageProxyUrl(
  owner: string,
  repo: string,
  number: number,
  src: string
): string {
  let parsed: URL
  try {
    parsed = new URL(src)
  } catch {
    return src
  }
  if (
    parsed.protocol !== "https:" ||
    !GITHUB_IMAGE_HOST_RE.test(parsed.hostname)
  ) {
    return src
  }
  if (
    /^(?:www\.)?github\.com$/i.test(parsed.hostname) &&
    !parsed.pathname.startsWith("/user-attachments/")
  ) {
    return src
  }
  const path = `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/image`
  return `${API_BASE}/dashboard/api${path}?url=${encodeURIComponent(src)}`
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message)
    this.name = "ApiError"
  }
}

export function isGithubReauthError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.status === 401) return true
  return /github token|re-login required/i.test(error.message)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/dashboard/api${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
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
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export interface PRTraceResolutionResult {
  resolved: boolean
  detail: string
  project: string | null
  thread_id: string | null
  confidence: number | null
  evidence: Array<string>
  trace_url: string | null
  run_count: number
  first_turn: string | null
  last_turn: string | null
}

export interface SessionUser {
  login: string
  email: string | null
  avatar_url: string | null
  auth_source?: string
  is_admin: boolean
  slack_oauth_enabled?: boolean
}

export interface PasswordLoginBody {
  email: string
  password: string
}

export interface PasswordResetRequestBody {
  email: string
}

export interface PasswordResetConfirmBody {
  token: string
  password: string
}

export interface ModelOption {
  id: string
  label: string
  efforts: Array<string>
  default_effort: string
  supports_images: boolean
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
  create_prs?: boolean
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
  create_prs?: boolean
  review_draft_prs?: boolean | null
}

export interface TeamSettings {
  review_draft_prs: boolean
  pr_summaries: boolean
  review_trace_links: boolean
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
  updated_at?: string | null
}

export interface DeliveryProjectSummary {
  project_id: string
  name: string
  active: boolean
  kill_switch: boolean
  tracker: {
    provider?: string
    config?: Record<string, unknown>
  }
  vcs: {
    provider?: string
    config?: Record<string, unknown>
  }
  queue_eligibility_policy: Record<string, unknown>
  sandbox_profile: Record<string, unknown>
  branch_policy: Record<string, unknown>
  gate_policy: Record<string, unknown>
  merge_policy: Record<string, unknown>
  run_limits: Record<string, unknown>
  member_logins: Array<string>
  delivery_modes: Array<string>
  latest_runs: Array<{
    id?: string
    status?: string
    title?: string
    provider?: string
    external_work_item_id?: string
    thread_id?: string
    pull_request_url?: string
    updated_at?: string
  }>
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

export interface CurrentsCredentialStatus {
  connected: boolean
  api_key_last4?: string
  updated_at?: string | null
}

export interface CurrentsConnectBody {
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

export interface UserMappingsPage {
  items: Array<UserMapping>
  total: number
  page: number
  page_size: number
}

export type UsageLeaderboardPeriod = "7d" | "30d" | "all"

export interface UsageLeaderboardRow {
  rank: number
  user: {
    name: string
    github_login: string | null
    email: string | null
  }
  favorite_model: string
  agent_runs: number
  prs_opened: number
  merged_prs: number
  agent_loc: number
  additions: number
  deletions: number
}

export interface ReviewerStatsCounterRow {
  name: string
  count: number
}

export interface ReviewerStatsPayload {
  period: UsageLeaderboardPeriod
  reviewed_prs: number
  prs_with_findings: number
  findings_recorded: number
  surfaced_findings: number
  addressed_findings: number
  resolved_after_update: number
  dismissed_findings: number
  unresolved_surfaced_findings: number
  resolution_rate: number
  human_replies: number
  severity_counts: Record<string, number>
  top_categories: Array<ReviewerStatsCounterRow>
  generated_at_ms: number | null
}

export interface UsageLeaderboardPayload {
  period: UsageLeaderboardPeriod
  rows: Array<UsageLeaderboardRow>
  total_members: number
  current_user_rank: number | null
  generated_at_ms: number | null
  reviewer_stats: ReviewerStatsPayload
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

export type ReviewStyleStatus = "idle" | "running" | "completed" | "failed"

export interface ReviewStyle {
  full_name: string
  owner?: string
  name?: string
  status: ReviewStyleStatus
  custom_prompt: string | null
  analysis_summary: string | null
  top_reviewers: Array<string>
  prs_sampled: number
  reviews_sampled: number
  analysis_thread_id: string | null
  analysis_run_id: string | null
  error: string | null
  created_by?: string
  created_at?: string
  updated_at?: string
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

export type FindingSeverity = "low" | "medium" | "high" | "critical"
export type FindingConfidence = "low" | "medium" | "high"
export type FindingStatus = "open" | "resolved" | "dismissed"
export type FindingGroup = "bug" | "investigate" | "informational"

export interface FindingInteraction {
  kind: "human_reply" | "bot_reply"
  author?: string
  body?: string
  created_at?: string
}

export interface ReviewFinding {
  id: string
  severity: FindingSeverity
  confidence: FindingConfidence
  category: string
  title: string
  description: string
  suggestion: string | null
  file: string
  start_line: number | null
  end_line: number | null
  side: "LEFT" | "RIGHT"
  in_diff: boolean
  status: FindingStatus
  outdated: boolean
  resolution_note: string | null
  diff_hunk: string | null
  github_thread_resolved: boolean
  github_review_comment_id: number | null
  interactions: Array<FindingInteraction>
  group: FindingGroup
}

export interface ReviewCommentCreate {
  path: string
  line: number
  side: "LEFT" | "RIGHT"
  body: string
  start_line?: number | null
  start_side?: "LEFT" | "RIGHT" | null
}

export interface ReviewCommentResult {
  id: number
  html_url: string
}

export interface PrReviewComment {
  id: number
  author: string
  author_avatar_url: string
  path: string
  line: number | null
  side: "LEFT" | "RIGHT"
  body: string
  html_url: string
  created_at: string
  is_open_swe: boolean
  // Outdated: the line no longer appears in the current diff, so it can't render inline.
  is_outdated: boolean
}

export interface ReviewCommentsPayload {
  comments: Array<PrReviewComment>
}

export interface ReviewCounts {
  open: number
  resolved: number
  dismissed: number
  bugs: number
  flags: number
}

export interface ReviewSummary {
  thread_id: string
  owner: string
  repo: string
  number: number
  title: string
  url: string
  head_ref: string
  base_ref: string
  author: string
  head_sha: string
  watch: boolean
  status: "running" | "error" | "idle"
  counts: ReviewCounts
  updated_at: string | null
  full_name?: string
}

export interface ReviewListPayload {
  reviews: Array<ReviewSummary>
  page: number
  has_more: boolean
}

export interface ReviewUserRef {
  login: string
  avatar_url?: string | null
}

export interface ReviewCheckRun {
  name: string
  status: string
  conclusion: string | null
  url: string | null
}

export interface ReviewPrDetails {
  state: string
  title: string
  body: string
  additions: number
  deletions: number
  changed_files: number
  commits: number
  head_sha: string
  head_ref: string
  base_ref: string
  author: ReviewUserRef | null
  assignees: Array<ReviewUserRef>
  requested_reviewers: Array<ReviewUserRef>
  labels: Array<{ name: string; color: string | null }>
}

export interface ReviewDiffGroup {
  index: number
  title: string
  summary: string
  files: Array<string>
}

export interface ReviewDetail extends ReviewSummary {
  pr: ReviewPrDetails
  checks: Array<ReviewCheckRun>
  findings: Array<ReviewFinding>
  diff_groups: Array<ReviewDiffGroup>
  diff_groups_stale: boolean
}

export interface ReviewDiffFile {
  path: string
  previousPath: string | null
  status: "added" | "removed" | "modified" | "renamed"
  additions: number
  deletions: number
  originalContent: string
  modifiedContent: string
  unrenderable?: boolean
}

export interface ReviewDiffPayload {
  files: Array<ReviewDiffFile>
  total_additions: number
  total_deletions: number
  truncated: boolean
}

export interface ReviewChatMeta {
  available: boolean
  assistant_id: string
}

export interface ReviewChatThread {
  thread_id: string
  title: string
  updated_at?: string | null
}

/**
 * Absolute base URL for the PR chat's LangGraph StreamProvider. The SDK builds
 * request URLs as `new URL(apiUrl + path)`, so this must be absolute — a
 * same-origin base is promoted using the current origin.
 */
export function reviewChatApiBase(
  owner: string,
  repo: string,
  number: number
): string {
  const path = `${API_BASE}/dashboard/api/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/chat`
  if (/^https?:\/\//.test(path)) return path
  if (typeof window !== "undefined") {
    return `${window.location.origin}${path.startsWith("/") ? "" : "/"}${path}`
  }
  return path
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

export const api = {
  me: () => request<SessionUser>("/me"),
  passwordLogin: (body: PasswordLoginBody) =>
    request<void>("/auth/password/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  requestPasswordReset: (body: PasswordResetRequestBody) =>
    request<{ status: string }>("/auth/password/reset/request", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  confirmPasswordReset: (body: PasswordResetConfirmBody) =>
    request<void>("/auth/password/reset/confirm", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  options: () => request<OptionsPayload>("/options"),
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
    request<ReviewStyle>(
      `/review-styles/${encodeURIComponent(full_name)}/analyze`,
      {
        method: "POST",
      }
    ),
  cancelReviewStyle: (full_name: string) =>
    request<ReviewStyle>(
      `/review-styles/${encodeURIComponent(full_name)}/cancel`,
      {
        method: "POST",
      }
    ),
  deleteReviewStyle: (full_name: string) =>
    request<void>(`/review-styles/${encodeURIComponent(full_name)}`, {
      method: "DELETE",
    }),
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
      {
        method: "PUT",
        body: JSON.stringify({ instructions }),
      }
    ),
  deleteAgentInstructions: (full_name: string) =>
    request<void>(`/agent-instructions/${encodeURIComponent(full_name)}`, {
      method: "DELETE",
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
  getTeamSettings: () => request<TeamSettings>("/team-settings"),
  saveTeamSettings: (body: TeamSettings) =>
    request<TeamSettings>("/team-settings", {
      method: "PUT",
      body: JSON.stringify(body),
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
  getMyCurrentsStatus: () =>
    request<CurrentsCredentialStatus>("/my-credentials/currents"),
  connectCurrents: (body: CurrentsConnectBody) =>
    request<CurrentsCredentialStatus>("/my-credentials/currents", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  disconnectCurrents: () =>
    request<CurrentsCredentialStatus>("/my-credentials/currents", {
      method: "DELETE",
    }),
  getMyNotionStatus: () =>
    request<NotionCredentialStatus>("/my-credentials/notion"),
  disconnectNotion: () =>
    request<NotionCredentialStatus>("/my-credentials/notion", {
      method: "DELETE",
    }),
  listDeliveryProjects: () =>
    request<{ items: Array<DeliveryProjectSummary> }>("/delivery-projects"),
  listEnabledReviewRepos: () =>
    request<{ repos: Array<string> }>("/enabled-review-repos"),
  setEnabledReviewRepo: (full_name: string, enabled: boolean) =>
    request<{ repos: Array<string> }>("/enabled-review-repos", {
      method: "PUT",
      body: JSON.stringify({ full_name, enabled }),
    }),
  usageLeaderboard: (period: UsageLeaderboardPeriod = "30d", limit = 10) =>
    request<UsageLeaderboardPayload>(
      `/agent-usage-leaderboard?period=${encodeURIComponent(period)}&limit=${limit}`
    ),
  myMapping: () => request<Partial<UserMapping>>("/my-mapping"),
  adminListUserMappings: (page = 1, pageSize = 20) =>
    request<UserMappingsPage>(
      `/admin/user-mappings?page=${page}&page_size=${pageSize}`
    ),
  adminDeleteUserMapping: (github_login: string) =>
    request<{ deleted: boolean }>(
      `/admin/user-mappings/${encodeURIComponent(github_login)}`,
      { method: "DELETE" }
    ),
  listReviews: (page: number, mine: boolean) =>
    request<ReviewListPayload>(`/reviews?page=${page}&mine=${mine}`),
  getReview: (owner: string, repo: string, number: number) =>
    request<ReviewDetail>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}`
    ),
  getReviewDiff: (owner: string, repo: string, number: number) =>
    request<ReviewDiffPayload>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/diff`
    ),
  getReviewChat: (owner: string, repo: string, number: number) =>
    request<ReviewChatMeta>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/chat`
    ),
  listReviewChatThreads: (owner: string, repo: string, number: number) =>
    request<{ threads: Array<ReviewChatThread> }>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/chat/threads`
    ),
  deleteReviewChatThread: (
    owner: string,
    repo: string,
    number: number,
    threadId: string
  ) =>
    request<void>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/chat/threads/${encodeURIComponent(threadId)}`,
      { method: "DELETE" }
    ),
  reReview: (owner: string, repo: string, number: number) =>
    request<{
      success: boolean
      queued: boolean
      thread_id: string
      pr_url: string
    }>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/re-review`,
      { method: "POST" }
    ),
  resolveTrace: (owner: string, repo: string, number: number) =>
    request<PRTraceResolutionResult>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/resolve-trace`,
      { method: "POST" }
    ),
  createReviewComment: (
    owner: string,
    repo: string,
    number: number,
    body: ReviewCommentCreate
  ) =>
    request<ReviewCommentResult>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/comments`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  listReviewComments: (owner: string, repo: string, number: number) =>
    request<ReviewCommentsPayload>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/comments`
    ),
  getReviewerEval: () => request<ReviewerEvalStatus>("/admin/evals/reviewer"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
}

export function loginUrl(redirectTo?: string): string {
  const target =
    redirectTo ?? (typeof window !== "undefined" ? window.location.origin : "")
  const qs = target ? `?redirect_to=${encodeURIComponent(target)}` : ""
  return `${API_BASE}/dashboard/api/auth/login${qs}`
}

export function slackConnectUrl(): string {
  return `${API_BASE}/dashboard/api/slack/login`
}

export function notionConnectUrl(): string {
  return `${API_BASE}/dashboard/api/notion/login`
}
