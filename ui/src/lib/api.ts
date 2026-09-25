/**
 * Typed client for the open-swe dashboard backend.
 *
 * All requests are sent with credentials so the httpOnly `osw_session`
 * cookie set by the OAuth callback rides along on cross-origin calls.
 */

import { dashboardApiBase } from "./api-base"
import {
  DashboardRequestError,
  REQUEST_ID_HEADER,
  dashboardApiUrl,
  dashboardForwardedHeaders,
  networkError,
  newRequestId,
} from "./dashboard-fetch"

const API_BASE = dashboardApiBase()

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

export interface ClientErrorReport {
  error_id: string
  title: string
  error_message: string
  status: number | null
  mutation: string | null
  path: string
}

export class ApiError extends DashboardRequestError {
  constructor(status: number, message: string, requestId?: string) {
    super(status, message, requestId)
    this.name = "ApiError"
  }
}

export function isGithubReauthError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.status === 401) return true
  return /github token|re-login required/i.test(error.message)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const requestId = newRequestId()
  const res = await fetch(dashboardApiUrl(path), {
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
    throw new ApiError(res.status, message, requestId)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

let activePrDetails = 0
const pendingPrDetails: Array<() => void> = []

async function loadPrDetails(
  repo: string,
  number: number
): Promise<OpenPullRequest | null> {
  await new Promise<void>((resolve) => {
    const start = () => {
      activePrDetails++
      resolve()
    }
    if (activePrDetails < 4) start()
    else pendingPrDetails.push(start)
  })
  try {
    return await request<OpenPullRequest | null>(
      `/repos/${repo.split("/").map(encodeURIComponent).join("/")}/pulls/${number}`
    )
  } finally {
    activePrDetails--
    pendingPrDetails.shift()?.()
  }
}

export interface SessionUser {
  login: string
  email: string | null
  avatar_url: string | null
  user_id?: string | null
  slack_user_id?: string | null
  is_admin: boolean
  /** Mirrors the user's preference: what Enter does while a run is live. */
  follow_up_behavior?: FollowUpBehavior
  /** Whether the server records new threads into the transcript log. */
  transcript_recording?: boolean
  slack_oauth_enabled?: boolean
  build_info?: BuildInfo
  api_base_url?: string
  slack_base_url?: string
}

/** Identifiers an artifact discovered about itself; `null` means unavailable, never assumed. */
export interface BuildInfo {
  backend: {
    /** LangGraph Platform revision id — opaque, never a git SHA. */
    revision_id: string | null
    commit: string | null
    built_at: string | null
    package_version: string | null
  }
  dashboard: {
    commit: string | null
    built_at: string | null
    served: boolean
  }
}

/** Normalizes session build identifiers, including older backends with no field. */
export function normalizeBuildInfo(raw: unknown): BuildInfo | null {
  if (typeof raw !== "object" || raw === null) return null
  const backend = (raw as { backend?: unknown }).backend
  if (typeof backend !== "object" || backend === null) return null
  const b = backend as Partial<BuildInfo["backend"]>
  const dashboard = (raw as { dashboard?: unknown }).dashboard
  const d =
    typeof dashboard === "object" && dashboard !== null
      ? (dashboard as Partial<BuildInfo["dashboard"]>)
      : undefined
  return {
    backend: {
      revision_id: typeof b.revision_id === "string" ? b.revision_id : null,
      commit: typeof b.commit === "string" ? b.commit : null,
      built_at: typeof b.built_at === "string" ? b.built_at : null,
      package_version:
        typeof b.package_version === "string" ? b.package_version : null,
    },
    dashboard: {
      commit: d && typeof d.commit === "string" ? d.commit : null,
      built_at: d && typeof d.built_at === "string" ? d.built_at : null,
      served: typeof d?.served === "boolean" ? d.served : false,
    },
  }
}

export interface ModelOption {
  id: string
  label: string
  efforts: Array<string>
  default_effort: string
  supports_images: boolean
  can_be_default?: boolean
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
  experimental_assistant_ui?: boolean | null
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
  model_routing_enabled?: boolean
  recent_thread_context_enabled?: boolean
  concierge_mode?: boolean
  experimental?: ExperimentalFeatures
  draft_prs?: boolean
  review_draft_prs?: boolean | null
  slack_onboarding_dismissed?: boolean
  updated_at?: string
}

/** Opt-in features; each thread keeps the values its creator had when it started. */
export interface ExperimentalFeatures {
  pr_comment_triggers?: boolean
}

export interface ProfileUpdate {
  experimental_assistant_ui?: boolean | null
  default_model: string
  reasoning_effort: string
  default_subagent_model?: string | null
  subagent_reasoning_effort?: string | null
  default_repo?: string | null
  base_branch?: string | null
  branch_prefix?: string | null
  auto_fix_ci?: boolean
  model_routing_enabled?: boolean | null
  recent_thread_context_enabled?: boolean
  concierge_mode?: boolean
  experimental?: ExperimentalFeatures
  draft_prs?: boolean
  review_draft_prs?: boolean | null
  slack_onboarding_dismissed?: boolean
}

export interface SlackBotOption {
  team_id: string
  bot_id: string
  user_id: string
  name: string
  image_url: string
}

export interface AllowedSlackBot {
  team_id: string
  bot_id: string
  user_id: string
  app_id: string
  name: string
  created_by: string
  created_at: string
  image_url: string
}

/** The settings record at either tier: the instance, or what a workspace's runs see. */
export interface WorkspaceSettings {
  review_draft_prs: boolean
  pr_summaries: boolean
  review_trace_links: boolean
  /** Tri-state adaptive model routing toggle; user preference overrides this org default. */
  model_routing_enabled?: boolean | null
  /** Tri-state LLM Gateway toggle; null inherits the LANGSMITH_GATEWAY_ENABLED default. */
  gateway_enabled?: boolean | null
  fable_enabled?: boolean
  /** Experimental: approve and merge tiny PRs from their Slack thread. Off by default. */
  expedited_review_enabled?: boolean
  org_guidelines?: string | null
  review_auto_approve?: boolean
  approval_policy?: string | null
  default_agent_model?: string | null
  default_agent_reasoning_effort?: string | null
  default_agent_subagent_model?: string | null
  default_agent_subagent_reasoning_effort?: string | null
  default_agent_routing_fast_model?: string | null
  default_agent_routing_fast_reasoning_effort?: string | null
  default_agent_routing_balanced_model?: string | null
  default_agent_routing_balanced_reasoning_effort?: string | null
  default_agent_routing_performance_model?: string | null
  default_agent_routing_performance_reasoning_effort?: string | null
  default_repo?: string | null
  default_reviewer_model?: string | null
  default_reviewer_reasoning_effort?: string | null
  default_reviewer_subagent_model?: string | null
  default_reviewer_subagent_reasoning_effort?: string | null
  default_chat_model?: string | null
  default_chat_reasoning_effort?: string | null
  default_thread_title_model?: string | null
  default_thread_title_reasoning_effort?: string | null
  updated_at?: string | null
}

/** The fields of a workspace's own settings record; anything absent inherits the instance value. */
export type WorkspaceSettingsOverrides = Partial<WorkspaceSettings>

/** One workspace's settings: what its runs see, and which of those values it set itself. */
export interface WorkspaceSettingsView {
  effective: WorkspaceSettings
  overrides: WorkspaceSettingsOverrides
}

export interface MCPOAuth {
  grant_type?: "client_credentials"
  token_url: string
  client_id: string
  scope?: string
  token_endpoint_auth_method?: "client_secret_post" | "client_secret_basic"
}

export type MCPOAuthUpdate = MCPOAuth & {
  client_secret?: string | null
}

export interface MCPConnection {
  name: string
  url: string
  transport: "streamable_http" | "sse"
  enabled: boolean
  allowed_tools: string[]
  header_names: string[]
  oauth?: MCPOAuth | null
  revision: string
  updated_at: string
}

export interface MCPConnectionUpdate {
  name: string
  url: string
  transport: MCPConnection["transport"]
  enabled: boolean
  allowed_tools: string[]
  headers?: Record<string, string> | null
  oauth?: MCPOAuthUpdate | null
}

export interface NotionCredentialStatus {
  connected: boolean
  token_expires_at?: string | null
  updated_at?: string | null
}

export interface AdminUser {
  user_id: string
  github_login: string
  email: string
  slack_user_id: string | null
  display_name: string
  is_admin: boolean
}

export interface AdminUsersPage {
  items: Array<AdminUser>
  total: number
  page: number
  page_size: number
}

export type UsageLeaderboardPeriod = "24h" | "7d" | "30d" | "all"

/** Origin + mount path only, so diagnostics can name the API without tokens or query data. */
export function describeApiBase(apiBaseUrl: string | undefined): {
  origin: string | null
  path: string
} {
  const path = `${dashboardApiBase()}/dashboard/api`
  if (!apiBaseUrl) return { origin: null, path }
  try {
    return { origin: new URL(apiBaseUrl).origin, path }
  } catch {
    return { origin: null, path }
  }
}

export type UsageLeaderboardSort =
  | "rank"
  | "user"
  | "favorite_model"
  | "invocations"
  | "threads"
  | "avg_invocations_per_thread"
  | "total_tokens"
  | "total_cost_usd"
  | "avg_invocation_seconds"
  | "avg_thread_seconds"
  | "prs_opened"
  | "merged_prs"
  | "merged_prs_per_thread"
  | "agent_loc"
  | "feedback_given"
export type SortDirection = "asc" | "desc"

export interface AnalyticsMetadata {
  reporting_cutover_at: string
  collection_started_at: string | null
  last_processed_at: string | null
  data_source: "event_projections"
  completeness: "not_started" | "observed_events_only"
  has_pending_events: boolean
  has_failed_events: boolean
  as_of: string
  /** Absent on backends that predate build reporting. */
  build_info?: BuildInfo
}

export interface UsageLeaderboardRow {
  rank: number
  user: {
    name: string
    github_login: string | null
    email: string | null
    avatar_url?: string | null
  }
  favorite_model: string
  favorite_model_effort?: string | null
  invocations: number
  threads?: number
  /** @deprecated Rolling compatibility with older clients. */
  agent_runs?: number
  prs_opened: number
  merged_prs: number
  merged_prs_per_thread?: number
  agent_loc: number
  feedback_given: number
  is_top_feedback_contributor?: boolean
  additions: number
  deletions: number
  total_tokens: number
  total_cost_usd: number
  invocations_without_cost?: number
  invocations_with_partial_cost?: number
  avg_invocation_seconds: number
  avg_thread_seconds?: number
  /** @deprecated Rolling compatibility with older clients. */
  avg_run_seconds?: number
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

export interface UsageLeaderboardPayload extends AnalyticsMetadata {
  period: UsageLeaderboardPeriod
  rows: Array<UsageLeaderboardRow>
  total_members: number
  next_cursor?: string | null
  current_user_rank: number | null
  generated_at_ms: number | null
  reviewer_stats: ReviewerStatsPayload
}

export interface PRMergeRateEffort {
  effort: string | null
  merged: number
  closed_without_merge: number
  mature_pending: number
  waiting: number
  cohort_size: number
  decided_denominator: number
  decided_merge_rate: number | null
  mature_denominator: number
  mature_cohort_merge_share: number | null
}

export interface PRMergeRateCohort {
  model_id: string | null
  model_attribution_quality: "effective" | "configured" | "unavailable"
  merged: number
  closed_without_merge: number
  mature_pending: number
  waiting: number
  cohort_size: number
  decided_denominator: number
  decided_merge_rate: number | null
  mature_denominator: number
  mature_cohort_merge_share: number | null
  avg_merge_seconds: number | null
  /**
   * Present-but-null means every PR in the group lacked valid timing; a missing
   * key (older backend) means the metric itself is unsupported. Zero is a real
   * measurement, distinct from both.
   */
  avg_delivery_seconds?: number | null
  efforts: PRMergeRateEffort[]
  median_distance_basis_points?: number | null
  distance_sample_size?: number
}

export interface PRMergeRatePayload extends AnalyticsMetadata {
  status: "ready" | "not_started" | "no_prs" | "suppressed"
  metric: "pr_outcomes_by_opening_invocation_configured_model"
  definition: string
  maturity_days: number
  period: UsageLeaderboardPeriod
  suppression_threshold: number
  cohorts: PRMergeRateCohort[]
  unavailable_thread_ids: string[]
}

/** The PR report plus when this browser last received it, kept apart from the server's `as_of`. */
export interface PRMergeRateResponse {
  payload: PRMergeRatePayload
  fetchedAt: string
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
  approval_policy?: string | null
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

export interface UserInstructions {
  login?: string
  instructions: string
  created_at?: string
  updated_at?: string
  updated_by?: string
}

export type ThreadVisibility = "public" | "private"
/** Queue holds a follow-up until the run ends; steer delivers it into the live run. */
export type FollowUpBehavior = "queue" | "steer"

export interface UserPreferences {
  default_visibility: ThreadVisibility
  local_tracing_project: string | null
  default_local_tracing_project: string
  default_workspace: string | null
  follow_up_behavior: FollowUpBehavior
}

export interface Skill {
  name: string
  description: string
  instructions: string
  created_at?: string
  updated_at?: string
}

export interface SkillInput {
  description: string
  instructions: string
}

export interface SkillsPage {
  items: Array<Skill>
  next_offset: number | null
}

export interface OrganizationSkillsPage {
  items: Array<Skill>
  next_cursor: string | null
}

/** What a non-admin needs to pick a workspace for a new thread. */
export type WorkspaceRefreshStatus =
  | "never"
  | "refreshing"
  | "success"
  | "failed"

/** One stage of a rebuild: booting the builder, a script, the capture. */
export interface WorkspaceRefreshStep {
  label: string
  status: "running" | "success" | "failed"
  started_at?: string
  finished_at?: string | null
  exit_code?: number | null
  log_path?: string | null
}

/** Slug of the workspace every deployment ships with; matches the backend's `DEFAULT_WORKSPACE_SLUG`. */
export const DEFAULT_WORKSPACE_SLUG = "default"

export interface WorkspaceOption {
  slug: string
  name: string
  repos: Array<string>
  /** Effective default repository, withheld when another workspace owns it. */
  default_repo: string | null
  slack_channel_ids: Array<string>
  is_default: boolean
  has_snapshot: boolean
  refresh_status?: WorkspaceRefreshStatus
  refresh_kind?: "full" | "update" | null
  refresh_finished_at?: string | null
  refresh_error?: string | null
  refresh_log_excerpt?: string | null
  refresh_steps?: Array<WorkspaceRefreshStep>
}

/** A channel the Slack bot can see, offered when binding channels to a workspace. */
export interface SlackChannelOption {
  id: string
  name: string
  is_private: boolean
  is_member: boolean
  is_ext_shared: boolean
  num_members: number | null
}

/** The channel directory; partial when Slack rate limited the walk over public channels. */
export interface SlackChannelDirectory {
  channels: Array<SlackChannelOption>
  partial: boolean
}

export interface WorkspaceOptionList {
  workspaces: Array<WorkspaceOption>
  default_slug: string
}

/** Body for `POST /workspaces`; `name` is the only required field. */
export interface WorkspaceCreate {
  name: string
  prompt?: string
  repos?: Array<string>
  slack_channel_ids?: Array<string>
}

/** Body for `PUT /workspaces/{slug}`. Only the fields present are changed. */
export interface WorkspaceUpdate {
  name?: string
  prompt?: string
  repos?: Array<string>
  slack_channel_ids?: Array<string>
  setup_script?: string
  update_script?: string
}

export type WorkspaceSnapshotStatus = "none" | "capturing" | "ready" | "failed"

/**
 * A workspace as `GET /workspaces/{slug}` returns it. The first five fields
 * are what `createWorkspace`/`updateWorkspace` guarantee; the rest describe
 * the sandbox image and its last rebuild.
 */
export interface WorkspaceRecord {
  slug: string
  name: string
  prompt: string
  repos: Array<string>
  slack_channel_ids: Array<string>
  setup_script?: string
  update_script?: string
  base_snapshot_id?: string | null
  snapshot_id?: string | null
  snapshot_name?: string | null
  snapshot_status?: WorkspaceSnapshotStatus
  status_message?: string | null
  mem_bytes?: number | null
  vcpus?: number | null
  fs_capacity_bytes?: number | null
  refresh_status?: WorkspaceRefreshStatus
  refresh_kind?: "full" | "update" | null
  refresh_finished_at?: string | null
  refresh_error?: string | null
}

/** How one repository is configured inside a workspace. */
export interface RepositorySettings {
  repo: string
  may_start_threads: boolean
}

/** What `POST /workspaces/{slug}/refresh` answers. */
export interface WorkspaceRefreshStart {
  started: boolean
  run_id: string
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

export type PullRequestReviewEvent = "APPROVE" | "REQUEST_CHANGES" | "COMMENT"

export interface PendingReviewComment {
  id: number
  node_id: string
  path: string
  line: number | null
  start_line: number | null
  side: "LEFT" | "RIGHT" | null
  start_side: "LEFT" | "RIGHT" | null
  body: string
}

/** The viewer's unsubmitted GitHub review; its comments post together on submit. */
export interface PendingReview {
  id: number
  node_id: string
  comments: Array<PendingReviewComment>
}

export interface SubmittedReview {
  id: number
  html_url: string
  state: string
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

export interface OpenPullRequest {
  detailsLoading?: boolean
  detailsError?: boolean
  repo: string
  number: number
  title: string
  draft: boolean | null
  additions: number | null
  deletions: number | null
  mergeable: boolean | null
  mergeState: string
  headSha: string | null
  headRef: string | null
  reviewDecision: "approved" | "changes_requested" | "none" | null
  // Branch protection still wants an approval this PR does not have.
  reviewRequired: boolean
  statusAvailable: boolean
  createdAt: string | null
  updatedAt: string | null
  ci: "passing" | "failing" | "pending" | "unknown" | "none"
  failingChecks: string[]
  pendingChecks: string[]
  // null when the review threads could not be read, which is not the same
  // answer as none being unresolved.
  unresolvedThreads: number | null
}

export type MergeMethod = "squash" | "merge" | "rebase"

export type PullRequestActionName =
  | "merge"
  | "close"
  | "mark-ready"
  | "update-branch"

export type PullRequestActionRequest =
  | { action: "merge"; sha: string | null; merge_method: MergeMethod }
  | { action: "close"; reason?: string }
  | { action: "mark-ready" }
  | { action: "update-branch"; sha: string | null }

export interface PullRequestActionResult {
  action: PullRequestActionName
  done: boolean
}

export type PullRequestThreadIntent =
  | { intent: "open"; title: string }
  | { intent: "fix"; context: OpenPullRequest | null }
  | { intent: "address-comments" }
  | { intent: "address-comment"; comment_url: string; instructions: string }

export interface ResolveReviewThreadsResult {
  resolved: Array<string>
  failed: Array<string>
}

export interface PullRequestThreadResult {
  thread_id: string
  already_running: boolean
}

export interface OpenPullRequestsPayload {
  pullRequests: OpenPullRequest[]
  nextPage: number | null
  incomplete: boolean
  updatedAt: string
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

/** Inclusive `[start, end]` line numbers. */
export type ReviewLineRange = [number, number]

/** Added lines are head line numbers; deleted lines are merge-base line numbers. */
export interface ReviewWalkthroughFile {
  path: string
  added: Array<ReviewLineRange>
  deleted: Array<ReviewLineRange>
}

export interface ReviewWalkthroughStep {
  index: number
  title: string
  summary: string
  other: boolean
  files: Array<ReviewWalkthroughFile>
}

/** The review scout's reading order for the PR's current head. */
export interface ReviewWalkthrough {
  head_sha: string
  /** The scout's summary of what people asked for; empty when it wrote none. */
  human_input: string
  steps: Array<ReviewWalkthroughStep>
}

/** `status: "none"` is a PR the reviewer graph has never run on. */
export interface ReviewDetail extends Omit<
  ReviewSummary,
  "thread_id" | "status"
> {
  thread_id: string | null
  status: ReviewSummary["status"] | "none"
  assessment?: PublishedReviewAssessment | null
  pr: ReviewPrDetails
  checks: Array<ReviewCheckRun>
  findings: Array<ReviewFinding>
  walkthrough: ReviewWalkthrough | null
  /** A review scout is working on this head, so `walkthrough` is on its way. */
  walkthrough_running: boolean
  /** Why the latest scout run on this head failed, when it did. */
  walkthrough_error: string | null
  walkthrough_scout_thread_id: string | null
  /** What the running scout has done so far; set only while `walkthrough_running`. */
  walkthrough_progress: ScoutProgress | null
  /** Why the latest reviewer run failed, when `status` is `"error"`. */
  review_error: string | null
}

export interface ScoutAction {
  tool: string
  target: string | null
}

export interface ScoutProgress {
  steps: number
  /** The latest action is still executing. */
  running: boolean
  recent: Array<ScoutAction>
}

export interface PublishedReviewAssessment {
  approved?: boolean
  review_id: number
  head_sha: string
  risk_score: number
  decision: "would_approve" | "needs_human_review"
  explanation: string
}

export interface ReviewAssessmentFeedbackInput {
  rating: "helpful" | "unhelpful"
  comment: string
}

export interface ReviewAssessmentFeedback extends ReviewAssessmentFeedbackInput {
  login: string
  updated_at: string
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

export type PreviewFileStatus =
  | "added"
  | "removed"
  | "modified"
  | "renamed"
  | "copied"
  | "changed"
  | "unchanged"

export interface PreviewFile {
  path: string
  status: PreviewFileStatus
  additions: number
  deletions: number
}

export interface PreviewThread {
  thread_id: string | null
  author: string | null
  body: string
  path: string
  line: number | null
  url: string | null
}

export interface PreviewCheck {
  name: string
  status: string
  conclusion: string | null
  url: string | null
}

export interface PullRequestPreview {
  title: string
  body: string
  author: string | null
  author_avatar_url: string | null
  state: string
  draft: boolean
  head_ref: string
  base_ref: string
  commits: number
  additions: number
  deletions: number
  changed_files: number
  files: Array<PreviewFile>
  // null when GitHub could not answer, which is not the same as none unresolved
  // or no checks configured.
  unresolved: Array<PreviewThread> | null
  checks: Array<PreviewCheck> | null
  /** The review scout's summary of what people asked for; empty until one has run. */
  human_input: string
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
  thread_id: string
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

async function pullRequestAction(
  pr: OpenPullRequest,
  body: PullRequestActionRequest
): Promise<PullRequestActionResult> {
  const result = await request<PullRequestActionResult>(
    `/repos/${pr.repo.split("/").map(encodeURIComponent).join("/")}/pulls/${pr.number}/action`,
    { method: "POST", body: JSON.stringify(body) }
  )
  if (!result.done)
    throw new Error(
      "GitHub did not confirm the change. Refresh to check the PR."
    )
  return result
}

function pullRequestThread(
  repo: string,
  number: number,
  body: PullRequestThreadIntent
): Promise<PullRequestThreadResult> {
  return request<PullRequestThreadResult>(
    `/repos/${repo.split("/").map(encodeURIComponent).join("/")}/pulls/${number}/thread`,
    { method: "POST", body: JSON.stringify(body) }
  )
}

export const api = {
  me: () => request<SessionUser>("/me"),
  /** Model list and defaults for one workspace; model defaults are per workspace. */
  options: (workspace: string = DEFAULT_WORKSPACE_SLUG) =>
    request<OptionsPayload>(
      `/options?workspace=${encodeURIComponent(workspace)}`
    ),
  profile: () => request<Profile>("/profile"),
  saveProfile: (body: ProfileUpdate) =>
    request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),
  repos: (options?: { refresh?: boolean }) =>
    request<ReposPayload>(options?.refresh ? "/repos?refresh=true" : "/repos"),
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
  saveReviewApprovalPolicy: (
    full_name: string,
    approval_policy: string | null
  ) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}`, {
      method: "PUT",
      body: JSON.stringify({ approval_policy }),
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
  reportClientError: (report: ClientErrorReport) =>
    request<void>("/client-errors", {
      method: "POST",
      body: JSON.stringify(report),
      keepalive: true,
    }),
  getMyInstructions: () => request<UserInstructions>("/me/instructions"),
  saveMyInstructions: (instructions: string) =>
    request<UserInstructions>("/me/instructions", {
      method: "PUT",
      body: JSON.stringify({ instructions }),
    }),
  deleteMyInstructions: () =>
    request<void>("/me/instructions", { method: "DELETE" }),
  getMyPreferences: () => request<UserPreferences>("/me/preferences"),
  saveMyPreferences: (preferences: UserPreferences) =>
    request<UserPreferences>("/me/preferences", {
      method: "PUT",
      body: JSON.stringify(preferences),
    }),
  listSkills: (offset = 0) =>
    request<SkillsPage>(`/skills?limit=100&offset=${offset}`),
  createSkill: (name: string, body: SkillInput) =>
    request<Skill>("/skills", {
      method: "POST",
      body: JSON.stringify({ name, ...body }),
    }),
  saveSkill: (name: string, body: SkillInput) =>
    request<Skill>(`/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteSkill: (name: string) =>
    request<void>(`/skills/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  listOrganizationSkills: (cursor: string | null = null) =>
    request<OrganizationSkillsPage>(
      `/organization-skills?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`
    ),
  createOrganizationSkill: (name: string, body: SkillInput) =>
    request<Skill>("/organization-skills", {
      method: "POST",
      body: JSON.stringify({ name, ...body }),
    }),
  saveOrganizationSkill: (name: string, body: SkillInput) =>
    request<Skill>(`/organization-skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteOrganizationSkill: (name: string) =>
    request<void>(`/organization-skills/${encodeURIComponent(name)}`, {
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
  listWorkspaceOptions: () =>
    request<WorkspaceOptionList>("/workspaces/options"),
  getWorkspace: (slug: string) =>
    request<WorkspaceRecord>(`/workspaces/${encodeURIComponent(slug)}`),
  createWorkspace: (body: WorkspaceCreate) =>
    request<WorkspaceRecord>("/workspaces", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  refreshWorkspace: (slug: string) =>
    request<WorkspaceRefreshStart>(
      `/workspaces/${encodeURIComponent(slug)}/refresh`,
      { method: "POST" }
    ),
  updateWorkspace: (slug: string, body: WorkspaceUpdate) =>
    request<WorkspaceRecord>(`/workspaces/${encodeURIComponent(slug)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteWorkspace: (slug: string) =>
    request<void>(`/workspaces/${encodeURIComponent(slug)}`, {
      method: "DELETE",
    }),
  listWorkspaceRepositories: (slug: string) =>
    request<RepositorySettings[]>(
      `/workspaces/${encodeURIComponent(slug)}/repositories`
    ),
  configureWorkspaceRepository: (
    slug: string,
    repo: string,
    settings: { may_start_threads?: boolean }
  ) =>
    request<RepositorySettings>(
      `/workspaces/${encodeURIComponent(slug)}/repositories/${repo
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      { method: "PUT", body: JSON.stringify(settings) }
    ),
  /** The instance record every workspace inherits. */
  getInstanceSettings: () => request<WorkspaceSettings>("/settings"),
  getWorkspaceSettings: (slug: string) =>
    request<WorkspaceSettingsView>(
      `/workspaces/${encodeURIComponent(slug)}/settings`
    ),
  /** Replaces the workspace's overrides; a field left out inherits the instance value. */
  saveWorkspaceSettings: (
    slug: string,
    overrides: WorkspaceSettingsOverrides
  ) =>
    request<WorkspaceSettingsView>(
      `/workspaces/${encodeURIComponent(slug)}/settings`,
      { method: "PUT", body: JSON.stringify(overrides) }
    ),
  listSlackBots: () => request<SlackBotOption[]>("/slack/bots"),
  listSlackChannels: () => request<SlackChannelDirectory>("/slack/channels"),
  listAllowedSlackBots: () => request<AllowedSlackBot[]>("/slack/allowed-bots"),
  allowSlackBot: (body: { bot_id: string }) =>
    request<AllowedSlackBot>("/slack/allowed-bots", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  removeAllowedSlackBot: (teamId: string, botId: string) =>
    request<{ ok: boolean }>(
      `/slack/allowed-bots/${encodeURIComponent(teamId)}/${encodeURIComponent(botId)}`,
      { method: "DELETE" }
    ),
  saveInstanceSettings: (body: WorkspaceSettings) =>
    request<WorkspaceSettings>("/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getInstanceMCPs: () => request<MCPConnection[]>("/mcps"),
  revealInstanceMCPHeaders: (name: string) =>
    request<Record<string, string>>(
      `/mcps/${encodeURIComponent(name)}/headers/reveal`,
      { method: "POST", cache: "no-store" }
    ),
  saveInstanceMCP: (body: MCPConnectionUpdate) =>
    request<MCPConnection>(`/mcps/${encodeURIComponent(body.name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteInstanceMCP: (name: string) =>
    request<void>(`/mcps/${encodeURIComponent(name)}`, { method: "DELETE" }),
  discoverInstanceMCP: (body: MCPConnectionUpdate) =>
    request<{ name: string; description: string }[]>(
      `/mcps/${encodeURIComponent(body.name)}/discover`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  getWorkspaceMCPs: (workspace: string) =>
    request<MCPConnection[]>(
      `/workspaces/${encodeURIComponent(workspace)}/mcps`
    ),
  revealWorkspaceMCPHeaders: (workspace: string, name: string) =>
    request<Record<string, string>>(
      `/workspaces/${encodeURIComponent(workspace)}/mcps/${encodeURIComponent(name)}/headers/reveal`,
      { method: "POST", cache: "no-store" }
    ),
  saveWorkspaceMCP: (workspace: string, body: MCPConnectionUpdate) =>
    request<MCPConnection>(
      `/workspaces/${encodeURIComponent(workspace)}/mcps/${encodeURIComponent(body.name)}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      }
    ),
  deleteWorkspaceMCP: (workspace: string, name: string) =>
    request<void>(
      `/workspaces/${encodeURIComponent(workspace)}/mcps/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    ),
  discoverWorkspaceMCP: (workspace: string, body: MCPConnectionUpdate) =>
    request<{ name: string; description: string }[]>(
      `/workspaces/${encodeURIComponent(workspace)}/mcps/${encodeURIComponent(body.name)}/discover`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  getMyMCPs: () => request<MCPConnection[]>("/my-mcps"),
  revealMyMCPHeaders: (name: string) =>
    request<Record<string, string>>(
      `/my-mcps/${encodeURIComponent(name)}/headers/reveal`,
      { method: "POST", cache: "no-store" }
    ),
  saveMyMCP: (body: MCPConnectionUpdate) =>
    request<MCPConnection>(`/my-mcps/${encodeURIComponent(body.name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteMyMCP: (name: string) =>
    request<void>(`/my-mcps/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  discoverMyMCP: (body: MCPConnectionUpdate) =>
    request<{ name: string; description: string }[]>(
      `/my-mcps/${encodeURIComponent(body.name)}/discover`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  getMyNotionStatus: () =>
    request<NotionCredentialStatus>("/my-credentials/notion"),
  disconnectNotion: () =>
    request<NotionCredentialStatus>("/my-credentials/notion", {
      method: "DELETE",
    }),
  listAutoReviewRepos: () =>
    request<{ repos: Array<string> }>("/enabled-review-repos"),
  setAutoReviewRepo: (full_name: string, runAutomatically: boolean) =>
    request<{ repos: Array<string> }>("/enabled-review-repos", {
      method: "PUT",
      body: JSON.stringify({ full_name, enabled: runAutomatically }),
    }),
  usageLeaderboard: (
    period: UsageLeaderboardPeriod = "7d",
    limit = 10,
    cursor?: string,
    sort: UsageLeaderboardSort = "rank",
    direction: SortDirection = "asc"
  ) =>
    request<UsageLeaderboardPayload>(
      `/agent-usage-leaderboard?period=${encodeURIComponent(period)}&limit=${limit}&sort=${sort}&direction=${direction}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`
    ).then((payload) => ({
      ...payload,
      rows: payload.rows.map((row) => ({
        ...row,
        invocations: row.invocations ?? row.agent_runs ?? 0,
        avg_invocation_seconds:
          row.avg_invocation_seconds ?? row.avg_run_seconds ?? 0,
      })),
    })),
  prMergeRateByModel: (
    period: UsageLeaderboardPeriod = "7d",
    maturityDays?: number
  ) =>
    request<PRMergeRatePayload>(
      `/analytics/pr-merge-rate-by-model?period=${encodeURIComponent(period)}${maturityDays == null ? "" : `&maturity_days=${maturityDays}`}`
    ).then((payload) => ({ payload, fetchedAt: new Date().toISOString() })),
  adminListUsers: (page = 1, pageSize = 20) =>
    request<AdminUsersPage>(`/admin/users?page=${page}&page_size=${pageSize}`),
  listReviews: (page: number, mine: boolean) =>
    request<ReviewListPayload>(`/reviews?page=${page}&mine=${mine}`),
  myPullRequests: (
    repo: string,
    sort: "createdAt" | "updatedAt" = "updatedAt",
    direction: "asc" | "desc" = "desc",
    page = 1
  ) =>
    request<OpenPullRequestsPayload>(
      `/pull-requests?repo=${encodeURIComponent(repo)}&lightweight=true&sort=${sort === "createdAt" ? "created" : "updated"}&direction=${direction}&page=${page}&scope=mine`
    ),
  myPullRequestDetails: (repo: string, number: number) =>
    loadPrDetails(repo, number),
  fixPullRequest: (pr: OpenPullRequest) =>
    pullRequestThread(pr.repo, pr.number, { intent: "fix", context: pr }),
  addressPullRequestComments: (pr: OpenPullRequest) =>
    pullRequestThread(pr.repo, pr.number, { intent: "address-comments" }),
  addressPullRequestComment: (
    repo: string,
    number: number,
    commentUrl: string,
    instructions: string
  ) =>
    pullRequestThread(repo, number, {
      intent: "address-comment",
      comment_url: commentUrl,
      instructions,
    }),
  resolveReviewThreads: (
    repo: string,
    number: number,
    threadIds: Array<string>
  ) =>
    request<ResolveReviewThreadsResult>(
      `/repos/${repo.split("/").map(encodeURIComponent).join("/")}/pulls/${number}/review-threads/resolve`,
      { method: "POST", body: JSON.stringify({ thread_ids: threadIds }) }
    ),
  pullRequestThreadStatus: (repo: string, number: number) =>
    request<{ running: boolean }>(
      `/repos/${repo.split("/").map(encodeURIComponent).join("/")}/pulls/${number}/thread`
    ),
  openPullRequestThread: (repo: string, number: number, title: string) =>
    pullRequestThread(repo, number, { intent: "open", title }),
  mergePullRequest: (
    pr: OpenPullRequest,
    method: MergeMethod
  ): Promise<PullRequestActionResult> =>
    pullRequestAction(pr, {
      action: "merge",
      sha: pr.headSha,
      merge_method: method,
    }),
  closePullRequest: (
    pr: OpenPullRequest,
    reason?: string
  ): Promise<PullRequestActionResult> =>
    pullRequestAction(
      pr,
      reason ? { action: "close", reason } : { action: "close" }
    ),
  updatePullRequestBranch: (
    pr: OpenPullRequest
  ): Promise<PullRequestActionResult> =>
    pullRequestAction(pr, { action: "update-branch", sha: pr.headSha }),
  markPullRequestReady: (
    pr: OpenPullRequest
  ): Promise<PullRequestActionResult> =>
    pullRequestAction(pr, { action: "mark-ready" }),
  repoMergeMethods: (repo: string) =>
    request<{ mergeMethods: MergeMethod[] }>(
      `/repos/${repo.split("/").map(encodeURIComponent).join("/")}/merge-methods`
    ),
  reviewSummaries: (pullRequests: Array<{ repo: string; number: number }>) =>
    request<Record<string, ReviewSummary | null>>("/reviews/summaries", {
      method: "POST",
      body: JSON.stringify({ pullRequests }),
    }),
  getReview: (owner: string, repo: string, number: number) =>
    request<ReviewDetail>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}`
    ),
  getAssessmentFeedback: (
    owner: string,
    repo: string,
    number: number,
    reviewId: number
  ) =>
    request<ReviewAssessmentFeedback | null>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/feedback/${reviewId}`
    ),
  saveAssessmentFeedback: (
    owner: string,
    repo: string,
    number: number,
    reviewId: number,
    feedback: ReviewAssessmentFeedbackInput
  ) =>
    request<ReviewAssessmentFeedback>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/feedback/${reviewId}`,
      { method: "PUT", body: JSON.stringify(feedback) }
    ),
  getPullRequestPreview: (owner: string, repo: string, number: number) =>
    request<PullRequestPreview>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/preview`
    ),
  getReviewDiff: (owner: string, repo: string, number: number) =>
    request<ReviewDiffPayload>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/diff`
    ),
  getReviewChat: (owner: string, repo: string, number: number) =>
    request<ReviewChatMeta>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/chat`
    ),
  runReviewScout: (owner: string, repo: string, number: number) =>
    request<{ started: boolean; run_id: string | null }>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/scout`,
      { method: "POST" }
    ),
  markReviewViewed: (owner: string, repo: string, number: number) =>
    request<void>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/viewed`,
      { method: "POST" }
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
  getPendingReview: (owner: string, repo: string, number: number) =>
    request<PendingReview | null>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/pending-review`
    ),
  addPendingReviewComment: (
    owner: string,
    repo: string,
    number: number,
    comment: ReviewCommentCreate
  ) =>
    request<PendingReview>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/pending-review/comments`,
      { method: "POST", body: JSON.stringify(comment) }
    ),
  updatePendingReviewComment: (
    owner: string,
    repo: string,
    number: number,
    commentId: number,
    body: string
  ) =>
    request<PendingReview>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/pending-review/comments/${commentId}`,
      { method: "PATCH", body: JSON.stringify({ body }) }
    ),
  deletePendingReviewComment: (
    owner: string,
    repo: string,
    number: number,
    commentId: number
  ) =>
    request<PendingReview | null>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/pending-review/comments/${commentId}`,
      { method: "DELETE" }
    ),
  discardPendingReview: (owner: string, repo: string, number: number) =>
    request<{ discarded: boolean }>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/pending-review`,
      { method: "DELETE" }
    ),
  submitPullRequestReview: (
    owner: string,
    repo: string,
    number: number,
    review: { event: PullRequestReviewEvent; body: string }
  ) =>
    request<SubmittedReview>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/submit-review`,
      { method: "POST", body: JSON.stringify(review) }
    ),
  listReviewComments: (owner: string, repo: string, number: number) =>
    request<ReviewCommentsPayload>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/comments`
    ),
  updateReviewComment: (
    owner: string,
    repo: string,
    number: number,
    commentId: number,
    body: string
  ) =>
    request<ReviewCommentResult>(
      `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/comments/${commentId}`,
      { method: "PATCH", body: JSON.stringify({ body }) }
    ),
  getReviewerEval: () => request<ReviewerEvalStatus>("/admin/evals/reviewer"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
}

export function loginUrl(redirectTo?: string): string {
  const target =
    redirectTo ??
    (typeof window !== "undefined"
      ? `${window.location.pathname}${window.location.search}${window.location.hash}`
      : "")
  const qs = target ? `?redirect_to=${encodeURIComponent(target)}` : ""
  return `${API_BASE}/dashboard/api/auth/login${qs}`
}

/**
 * Start an OAuth connect flow, returning a promise only the desktop app has.
 *
 * The web flow is a redirect that comes back to the page that started it. The
 * desktop app can't use that: its window and the browser that shows the
 * provider's consent page have separate cookie jars, so it runs the flow
 * itself and resolves once the connection is stored.
 */
export function connectService(provider: "slack" | "notion") {
  const pending = window.openSweDesktop?.connectService(provider)
  if (!pending) {
    window.location.assign(`${API_BASE}/dashboard/api/${provider}/login`)
  }
  return pending
}
