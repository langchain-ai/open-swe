/**
 * Dashboard endpoints for PR reviews: the review itself (findings, diff, chat),
 * the PR comments the review page writes, and the per-repo review configuration
 * (auto-review opt-in and learned review styles).
 */

import { dashboardApiHref, request } from "@/lib/apiClient"

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

export interface ReReviewResult {
  success: boolean
  queued: boolean
  thread_id: string
  pr_url: string
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

function reviewPath(
  owner: string,
  repo: string,
  number: number,
  suffix = ""
): string {
  return `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}${suffix}`
}

export const reviewsApi = {
  list: (page: number, mine: boolean) =>
    request<ReviewListPayload>(`/reviews?page=${page}&mine=${mine}`),
  get: (owner: string, repo: string, number: number) =>
    request<ReviewDetail>(reviewPath(owner, repo, number)),
  getDiff: (owner: string, repo: string, number: number) =>
    request<ReviewDiffPayload>(reviewPath(owner, repo, number, "/diff")),
  reReview: (owner: string, repo: string, number: number) =>
    request<ReReviewResult>(reviewPath(owner, repo, number, "/re-review"), {
      method: "POST",
    }),
  resolveTrace: (owner: string, repo: string, number: number) =>
    request<PRTraceResolutionResult>(
      reviewPath(owner, repo, number, "/resolve-trace"),
      { method: "POST" }
    ),
  getChat: (owner: string, repo: string, number: number) =>
    request<ReviewChatMeta>(reviewPath(owner, repo, number, "/chat")),
  listChatThreads: (owner: string, repo: string, number: number) =>
    request<{ threads: Array<ReviewChatThread> }>(
      reviewPath(owner, repo, number, "/chat/threads")
    ),
  deleteChatThread: (
    owner: string,
    repo: string,
    number: number,
    threadId: string
  ) =>
    request<void>(
      reviewPath(
        owner,
        repo,
        number,
        `/chat/threads/${encodeURIComponent(threadId)}`
      ),
      { method: "DELETE" }
    ),
  listComments: (owner: string, repo: string, number: number) =>
    request<ReviewCommentsPayload>(
      reviewPath(owner, repo, number, "/comments")
    ),
  createComment: (
    owner: string,
    repo: string,
    number: number,
    body: ReviewCommentCreate
  ) =>
    request<ReviewCommentResult>(reviewPath(owner, repo, number, "/comments"), {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateComment: (
    owner: string,
    repo: string,
    number: number,
    commentId: number,
    body: string
  ) =>
    request<ReviewCommentResult>(
      reviewPath(owner, repo, number, `/comments/${commentId}`),
      { method: "PATCH", body: JSON.stringify({ body }) }
    ),
  listAutoReviewRepos: () =>
    request<{ repos: Array<string> }>("/enabled-review-repos"),
  setAutoReviewRepo: (full_name: string, runAutomatically: boolean) =>
    request<{ repos: Array<string> }>("/enabled-review-repos", {
      method: "PUT",
      body: JSON.stringify({ full_name, enabled: runAutomatically }),
    }),
  listStyles: () => request<Array<ReviewStyle>>("/review-styles"),
  createStyle: (full_name: string) =>
    request<ReviewStyle>("/review-styles", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    }),
  getStyle: (full_name: string) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}`),
  saveStylePrompt: (full_name: string, custom_prompt: string) =>
    request<ReviewStyle>(`/review-styles/${encodeURIComponent(full_name)}`, {
      method: "PUT",
      body: JSON.stringify({ custom_prompt }),
    }),
  analyzeStyle: (full_name: string) =>
    request<ReviewStyle>(
      `/review-styles/${encodeURIComponent(full_name)}/analyze`,
      { method: "POST" }
    ),
  cancelStyle: (full_name: string) =>
    request<ReviewStyle>(
      `/review-styles/${encodeURIComponent(full_name)}/cancel`,
      { method: "POST" }
    ),
  deleteStyle: (full_name: string) =>
    request<void>(`/review-styles/${encodeURIComponent(full_name)}`, {
      method: "DELETE",
    }),
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
  return `${dashboardApiHref(reviewPath(owner, repo, number, "/image"))}?url=${encodeURIComponent(src)}`
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
  const path = dashboardApiHref(reviewPath(owner, repo, number, "/chat"))
  if (/^https?:\/\//.test(path)) return path
  if (typeof window !== "undefined") {
    return `${window.location.origin}${path.startsWith("/") ? "" : "/"}${path}`
  }
  return path
}
