/**
 * Design QA's captures, as it keeps them in its own storage: one document per
 * capture in the repository-shared `captures` collection, under
 * `pulls/<number>/<id>` with a time-ordered (UUIDv7) `id`, so a pull
 * request's captures read newest first by key. Each scenario's `*_url`
 * fields name attachments of that document.
 */

export const CAPTURES = "captures"

export type CaptureKey = `pulls/${number}/${string}`

export const capturePrefix = (pull: number) => `pulls/${pull}/`

export const captureKey = (pull: number, id: string): CaptureKey =>
  `pulls/${pull}/${id}`

/** Where a pull request's visual capture stands. */
export type FrontendRunStatus =
  | "pending"
  | "running"
  | "complete"
  | "blocked"
  | "failed"

export interface PixelRect {
  x: number
  y: number
  width: number
  height: number
}

/** What the AI assessment noted about one area of a scenario's screenshots. */
export interface FrontendAnnotation extends PixelRect {
  title: string
  body: string
}

/** One page or interaction state, captured on the base preview and the pull request's. */
export interface FrontendScenario {
  id: string
  title: string
  path: string
  status: "complete" | "blocked" | "failed"
  /** The `*_url` fields name attachments of the capture's document. */
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
  annotations: FrontendAnnotation[]
  mask_rects: PixelRect[]
  tile_size: number
  min_region_pixels: number
  interaction: boolean
  /** The replay steps that reached an interaction state. */
  actions: string[]
  before_error: string | null
  after_error: string | null
  layout_warnings: string[]
  error: string | null
}

export interface FrontendReport {
  production_url: string | null
  summary: string
  ai_review_status: "not_run" | "running" | "complete" | "failed"
  /** The private thread the AI assessment ran in, for `nav.openThread`. */
  assessment_thread_id: string | null
  assessment_error: string | null
  build_verification: "unverified" | "verified"
  video_status: "unavailable" | "recorded"
  scenarios: FrontendScenario[]
}

/** One capture of a pull request's base and head previews. */
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
