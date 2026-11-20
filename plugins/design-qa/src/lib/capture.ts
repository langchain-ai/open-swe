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

/** How serious a note is: `error` breaks the design system or the page, `warning` looks off. */
export type Severity = "error" | "warning" | "info"

/** What the AI assessment noted about one area of a scenario's screenshots. */
export interface FrontendAnnotation extends PixelRect {
  title: string
  body: string
  severity: Severity
}

/** One place the change departs from the design system. */
export interface DesignSystemFinding {
  severity: Exclude<Severity, "info">
  title: string
  body: string
  /** Where the design system says so: a document and section, a component or a token. */
  rule: string
  /** The file and line in the diff, when the finding is in code. */
  location: string | null
  /** The scenario whose screenshots show it, when one does. */
  scenario: string | null
}

/** The review's check of every visual change against the repository's design system. */
export interface DesignSystemReview {
  /** The documents, skills and packages it checked against; empty when the repository has none. */
  sources: string[]
  findings: DesignSystemFinding[]
}

/** How risky the visual change is, from 1 (low) to 5 (high), as Robin's code review scores it. */
export interface RiskAssessment {
  score: 1 | 2 | 3 | 4 | 5
  reason: string
}

/** A place the diff shows up that the review mapped but couldn't shoot. */
export interface MissedSurface {
  title: string
  /** The browser path it lives at, when the review knew one. */
  path: string | null
  /** The gate that kept it hidden, such as a feature flag or missing data. */
  reason: string
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
  /** The same state in dark mode; null when the site has none. */
  before_dark_url: string | null
  after_dark_url: string | null
  before_video_url: string | null
  after_video_url: string | null
  width: number | null
  height: number | null
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

/** The color scheme a scenario's screenshots were taken in. */
export type ShotTheme = "light" | "dark"

/** A scenario's base and PR screenshots in one theme. */
export interface ScenarioShots {
  before: string | null
  after: string | null
  theme: ShotTheme
}

export interface FrontendReport {
  production_url: string | null
  /** A few short points, one per line starting with `- `. */
  summary: string
  /** Null for captures saved before reviews scored risk. */
  risk: RiskAssessment | null
  /** Null for captures saved before reviews checked the design system. */
  design_system: DesignSystemReview | null
  ai_review_status: "not_run" | "running" | "complete" | "failed"
  /** The private thread the AI assessment ran in, for `nav.openThread`. */
  assessment_thread_id: string | null
  assessment_error: string | null
  build_verification: "unverified" | "verified"
  video_status: "unavailable" | "recorded"
  /** The surfaces the review mapped from the diff but couldn't capture. */
  missed: MissedSurface[]
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

  has(key: string): boolean {
    return this.found[key] != null
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
              risk: StoredCapture.risk(reported.fields("risk")),
              design_system: reported.has("design_system")
                ? StoredCapture.designSystem(reported.fields("design_system"))
                : null,
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
              missed: StoredCapture.missed(reported.fields("coverage")),
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

  /** Whether `scenario` was also shot in dark mode on both sides. */
  static hasDark(scenario: FrontendScenario): boolean {
    return scenario.before_dark_url !== null && scenario.after_dark_url !== null
  }

  /** `scenario`'s screenshots in `theme`, or its light ones when it has no dark pair. */
  static shots(scenario: FrontendScenario, theme: ShotTheme): ScenarioShots {
    return theme === "dark" && StoredCapture.hasDark(scenario)
      ? {
          before: scenario.before_dark_url,
          after: scenario.after_dark_url,
          theme,
        }
      : {
          before: scenario.before_url,
          after: scenario.after_url,
          theme: "light",
        }
  }

  /** When a UUIDv7 id was made, from its first 48 bits; null for any other id. */
  static createdAt(id: string): string | null {
    const hex = id.replaceAll("-", "")
    if (!/^[0-9a-f]{32}$/i.test(hex) || hex[12] !== "7") return null
    return new Date(Number.parseInt(hex.slice(0, 12), 16)).toISOString()
  }

  private static risk(found: Fields): RiskAssessment | null {
    const score = found.count("score")
    if (score === null || !Number.isInteger(score) || score < 1 || score > 5)
      return null
    return {
      score: score as RiskAssessment["score"],
      reason: found.text("reason") ?? "",
    }
  }

  private static designSystem(found: Fields): DesignSystemReview {
    return {
      sources: found.texts("sources"),
      findings: found.list("findings").flatMap((value) => {
        const finding = Fields.of(value)
        const title = finding.text("title")
        if (title === null) return []
        return [
          {
            severity: finding.oneOf(
              "severity",
              ["error", "warning"],
              "warning"
            ),
            title,
            body: finding.text("body") ?? "",
            rule: finding.text("rule") ?? "",
            location: finding.text("location"),
            scenario: finding.text("scenario"),
          },
        ]
      }),
    }
  }

  private static missed(found: Fields): MissedSurface[] {
    return found.list("missed").flatMap((value) => {
      const surface = Fields.of(value)
      const path = surface.text("path")
      const title = surface.text("title") ?? path
      return title === null
        ? []
        : [
            {
              title,
              path,
              reason: surface.text("reason") ?? "",
            },
          ]
    })
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
      before_dark_url: found.text("before_dark_url"),
      after_dark_url: found.text("after_dark_url"),
      before_video_url: found.text("before_video_url"),
      after_video_url: found.text("after_video_url"),
      width: found.count("width"),
      height: found.count("height"),
      annotations: found.list("annotations").flatMap((note) => {
        const box = Fields.of(note)
        const rect = box.rect()
        const title = box.text("title")
        return rect && title !== null
          ? [
              {
                ...rect,
                title,
                body: box.text("body") ?? "",
                severity: box.oneOf(
                  "severity",
                  ["error", "warning", "info"],
                  "info"
                ),
              },
            ]
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
}
