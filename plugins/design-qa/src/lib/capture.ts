import type { JsonObject, JsonValue, StoredDocument } from "@openswe/plugin-sdk"

/**
 * Design QA's captures, as it keeps them in its own storage: one document per
 * capture in the repository-shared `captures` collection, under
 * `pulls/<number>/<id>` with a time-ordered (UUIDv7) `id`, so a pull
 * request's captures read newest first by key. Each scenario's `*_url`
 * fields name attachments of that document.
 *
 * Reviews write them, following `review.md`, and leave out what the key and
 * the store already say: `captureOf` fills in the id, the times and defaults.
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

/** How long a capture may stay unfinished before it reads as interrupted. */
export const UNFINISHED_AFTER_MS = 15 * 60 * 1000

type Json = JsonValue | undefined

/** The fields of one JSON object a review wrote, read as the types they should be. */
class Fields {
  private constructor(private readonly found: JsonObject) {}

  /** `value`'s fields; none when it is not an object. */
  static of(value: Json): Fields {
    return new Fields(
      value !== null && typeof value === "object" && !Array.isArray(value)
        ? value
        : {}
    )
  }

  fields(key: string): Fields {
    return Fields.of(this.found[key])
  }

  text(key: string): string | null {
    const value = this.found[key]
    return typeof value === "string" ? value : null
  }

  count(key: string): number | null {
    const value = this.found[key]
    return typeof value === "number" && Number.isFinite(value) ? value : null
  }

  list(key: string): JsonValue[] {
    const value = this.found[key]
    return Array.isArray(value) ? value : []
  }

  texts(key: string): string[] {
    return this.list(key).filter((item) => typeof item === "string")
  }

  oneOf<T extends string>(key: string, options: readonly T[], or: T): T {
    return options.find((option) => option === this.found[key]) ?? or
  }

  isTrue(key: string): boolean {
    return this.found[key] === true
  }

  /** These fields as a pixel rectangle; null when any side is missing. */
  rect(): PixelRect | null {
    const x = this.count("x")
    const y = this.count("y")
    const width = this.count("width")
    const height = this.count("height")
    return x === null || y === null || width === null || height === null
      ? null
      : { x, y, width, height }
  }
}

/** Captures as reviews store them, read with what the key and the store say. */
export class StoredCapture {
  /** A stored capture, with its id, times and defaults filled in. */
  static read(
    stored: StoredDocument<JsonObject, CaptureKey>
  ): FrontendReviewRun {
    const document = Fields.of(stored.document)
    const id = stored.key.split("/").at(-1) ?? stored.key
    const created =
      document.text("created_at") ??
      StoredCapture.createdAt(id) ??
      stored.updatedAt ??
      ""
    // A capture that never got as far as a report, such as a blocked one, has none.
    const reported =
      stored.document.report == null ? null : document.fields("report")
    const scenarios = (reported?.list("scenarios") ?? []).map((value, index) =>
      StoredCapture.scenario(value, index)
    )
    return {
      id,
      status: document.oneOf(
        "status",
        ["pending", "running", "complete", "blocked", "failed"],
        "complete"
      ),
      auth_mode: document.oneOf("auth_mode", ["dummy", "none"], "none"),
      base_url: document.text("base_url") ?? "",
      head_url: document.text("head_url") ?? "",
      paths: document.texts("paths").length
        ? document.texts("paths")
        : [...new Set(scenarios.map((scenario) => scenario.path))],
      base_sha: document.text("base_sha") ?? "",
      head_sha: document.text("head_sha") ?? "",
      created_at: created,
      updated_at: document.text("updated_at") ?? stored.updatedAt ?? created,
      error: document.text("error"),
      report:
        reported === null
          ? null
          : {
              production_url: reported.text("production_url"),
              summary: reported.text("summary") ?? "",
              ai_review_status: reported.oneOf(
                "ai_review_status",
                ["not_run", "running", "complete", "failed"],
                reported.text("summary") ? "complete" : "not_run"
              ),
              assessment_thread_id: reported.text("assessment_thread_id"),
              assessment_error: reported.text("assessment_error"),
              build_verification: reported.oneOf(
                "build_verification",
                ["unverified", "verified"],
                "unverified"
              ),
              video_status: reported.oneOf(
                "video_status",
                ["unavailable", "recorded"],
                "unavailable"
              ),
              scenarios,
            },
    }
  }

  /**
   * `run` as it stands at `now`: a capture still pending or running 15 minutes
   * after its last write was cut short, as a restart mid-save leaves it.
   */
  static settled(
    run: FrontendReviewRun,
    now: number = Date.now()
  ): FrontendReviewRun {
    const unfinished = run.status === "pending" || run.status === "running"
    if (!unfinished || now - Date.parse(run.updated_at) < UNFINISHED_AFTER_MS)
      return run
    return {
      ...run,
      status: "failed",
      error: "The capture was interrupted before its screenshots were saved.",
    }
  }

  /** When a UUIDv7 id was made, from its first 48 bits; null for any other id. */
  static createdAt(id: string): string | null {
    const hex = id.replaceAll("-", "")
    if (!/^[0-9a-f]{32}$/i.test(hex) || hex[12] !== "7") return null
    return new Date(Number.parseInt(hex.slice(0, 12), 16)).toISOString()
  }

  private static scenario(value: JsonValue, index: number): FrontendScenario {
    const found = Fields.of(value)
    const path = found.text("path") ?? "/"
    const actions = found.texts("actions")
    return {
      id: found.text("id") ?? `scenario-${index + 1}`,
      title: found.text("title") ?? path,
      path,
      status: found.oneOf(
        "status",
        ["complete", "blocked", "failed"],
        "complete"
      ),
      before_url: found.text("before_url"),
      after_url: found.text("after_url"),
      before_video_url: found.text("before_video_url"),
      after_video_url: found.text("after_video_url"),
      width: found.count("width"),
      height: found.count("height"),
      metrics: StoredCapture.metrics(found.fields("metrics")),
      regions: found.list("regions").flatMap((region) => {
        const box = Fields.of(region)
        const rect = box.rect()
        const pixels = box.count("pixels")
        return rect && pixels !== null ? [{ ...rect, pixels }] : []
      }),
      annotations: found.list("annotations").flatMap((note) => {
        const box = Fields.of(note)
        const rect = box.rect()
        const title = box.text("title")
        return rect && title !== null
          ? [{ ...rect, title, body: box.text("body") ?? "" }]
          : []
      }),
      mask_rects: found
        .list("mask_rects")
        .flatMap((box) => Fields.of(box).rect() ?? []),
      tile_size: found.count("tile_size") ?? 32,
      min_region_pixels: found.count("min_region_pixels") ?? 4,
      interaction: found.isTrue("interaction") || actions.length > 0,
      actions,
      before_error: found.text("before_error"),
      after_error: found.text("after_error"),
      layout_warnings: found.texts("layout_warnings"),
      error: found.text("error"),
    }
  }

  private static metrics(found: Fields): FrontendScenario["metrics"] {
    const changed = found.count("changed_pixels")
    const total = found.count("total_pixels")
    if (changed === null || total === null || total <= 0) return null
    return {
      changed_pixels: changed,
      total_pixels: total,
      changed_ratio: found.count("changed_ratio") ?? changed / total,
      threshold: found.count("threshold") ?? 0,
    }
  }
}
