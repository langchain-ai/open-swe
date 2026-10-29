import type { OpenPullRequest } from "@/lib/api"
import {
  dashboardApiUrl,
  dashboardForwardedHeaders,
} from "@/lib/dashboard-fetch"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"
import type { PixelRect } from "./pixels"

export type FrontendRunStatus =
  | "pending"
  | "running"
  | "complete"
  | "blocked"
  | "failed"

export interface FrontendAnnotation extends PixelRect {
  title: string
  body: string
}

export interface FrontendScenario {
  id: string
  title: string
  path: string
  status: FrontendRunStatus
  before_url: string | null
  after_url: string | null
  diff_url: string | null
  mask_url: string | null
  before_video_url: string | null
  after_video_url: string | null
  width: number | null
  height: number | null
  metrics: {
    changed_pixels: number
    total_pixels: number
    changed_ratio: number
    threshold: number
  } | null
  regions: Array<PixelRect & { pixels: number }>
  mask_rects: PixelRect[]
  tile_size: number
  min_region_pixels: number
  annotations: FrontendAnnotation[]
  interaction?: boolean
  actions?: string[]
  before_error?: string | null
  after_error?: string | null
  layout_warnings?: string[]
  error: string | null
}

export interface FrontendReport {
  summary: string
  ai_review_status: "not_run" | "running" | "complete" | "failed"
  assessment_thread_id?: string | null
  assessment_error?: string | null
  build_verification: "unverified" | "verified"
  video_status: "unavailable" | "recorded"
  scenarios: FrontendScenario[]
}

export interface FrontendReviewRun {
  id: string
  status: FrontendRunStatus
  auth_mode: "dummy" | "none"
  base_url: string
  head_url: string
  paths: string[]
  base_sha: string
  head_sha: string
  created_at: string
  updated_at: string
  error: string | null
  report: FrontendReport | null
}

export interface FrontendReviewInboxItem {
  pr: OpenPullRequest
  run: FrontendReviewRun
  requested_reviewers: string[]
  requested_teams: string[]
}

export interface FrontendReviewInbox {
  items: FrontendReviewInboxItem[]
  next_page: number | null
  incomplete: boolean
}

export interface FrontendReviewInput {
  base_url: string
  head_url: string
  paths: string[]
  auth_mode: "dummy" | "none"
}

function path(pr: PullRequestRef) {
  return `/reviews/${encodeURIComponent(pr.owner)}/${encodeURIComponent(pr.repo)}/${pr.number}/frontend-reviews`
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(dashboardApiUrl(url), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...dashboardForwardedHeaders(),
      ...init.headers,
    },
  })
  if (!response.ok) {
    const text = await response.text()
    let message = response.statusText || "Could not load frontend review"
    try {
      const body: unknown = JSON.parse(text)
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      )
        message = body.detail
    } catch {
      console.warn("Frontend review returned a non-JSON error", response.status)
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const frontendReviewApi = {
  inbox: (scope: "mine" | "review-requested", page: number) =>
    request<FrontendReviewInbox>(
      `/frontend-reviews/inbox?scope=${scope}&page=${page}`
    ),
  list: (pr: PullRequestRef) =>
    request<{ runs: FrontendReviewRun[] }>(path(pr)),
  get: (pr: PullRequestRef, id: string) =>
    request<FrontendReviewRun>(`${path(pr)}/${encodeURIComponent(id)}`),
  start: (pr: PullRequestRef, input: FrontendReviewInput) =>
    request<FrontendReviewRun>(path(pr), {
      method: "POST",
      body: JSON.stringify(input),
    }),
}

export function evidenceMediaUrl(url: string): string {
  const unsafeSegment = url.split("/").some((segment) => {
    try {
      const decoded = decodeURIComponent(segment)
      return decoded === "." || decoded === ".." || /[\\/?#]/.test(decoded)
    } catch {
      return true
    }
  })
  if (
    !/^\/dashboard\/api\/reviews\/[^/]+\/[^/]+\/\d+\/frontend-reviews\/[^/]+\/media\/[^/]+$/.test(
      url
    ) ||
    unsafeSegment
  )
    throw new Error(
      "Evidence must be served by the authenticated frontend review API."
    )
  return dashboardApiUrl(url.slice("/dashboard/api".length))
}

export function frontendReviewKey(pr: PullRequestRef) {
  return ["frontend-review", pr.owner, pr.repo, pr.number] as const
}
