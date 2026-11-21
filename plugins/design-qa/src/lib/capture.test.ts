import { describe, expect, it } from "vitest"

import {
  CaptureAttempt,
  type FrontendReviewRun,
  StoredCapture,
  UNFINISHED_AFTER_MS,
} from "./capture"

const run = (status: FrontendReviewRun["status"]): FrontendReviewRun => ({
  id: "0190",
  status,
  auth_mode: "dummy",
  base_url: "https://base.example.com",
  head_url: "https://pr-7.example.com",
  paths: ["/"],
  base_sha: "a",
  head_sha: "b",
  created_at: "2026-11-09T10:00:00Z",
  updated_at: "2026-11-09T10:00:00Z",
  error: null,
  report: null,
})

const saved = Date.parse("2026-11-09T10:00:00Z")

describe("StoredCapture.settled", () => {
  it("reads a capture unfinished for 15 minutes as interrupted", () => {
    const later = saved + UNFINISHED_AFTER_MS
    expect(StoredCapture.settled(run("running"), later)).toMatchObject({
      status: "failed",
      error: expect.stringContaining("interrupted"),
    })
    expect(StoredCapture.settled(run("pending"), later).status).toBe("failed")
  })

  it("leaves a capture still within its time, or finished, as it is", () => {
    expect(
      StoredCapture.settled(run("running"), saved + UNFINISHED_AFTER_MS - 1)
        .status
    ).toBe("running")
    const finished = run("complete")
    expect(
      StoredCapture.settled(finished, saved + 10 * UNFINISHED_AFTER_MS)
    ).toBe(finished)
  })
})

describe("StoredCapture.read", () => {
  it("fills in what a review leaves to the key and the store", () => {
    const id = "019a6a5c-7e00-7abc-8def-0123456789ab"
    const capture = StoredCapture.read({
      key: `pulls/7/${id}`,
      updatedAt: "2026-11-09T10:05:00Z",
      document: {
        base_url: "https://dev.example.com",
        head_url: "https://pr-7.example.com",
        head_sha: "b",
        report: {
          summary: "The header turned black.",
          scenarios: [
            {
              id: "home",
              path: "/",
              before_url: "home-before.png",
              after_url: "home-after.png",
              actions: ["Open the menu"],
            },
          ],
        },
      },
    })

    expect(capture).toMatchObject({
      id,
      status: "complete",
      created_at: StoredCapture.createdAt(id),
      updated_at: "2026-11-09T10:05:00Z",
      paths: ["/"],
      report: {
        ai_review_status: "complete",
        summary: "The header turned black.",
      },
    })
    expect(capture.report?.scenarios[0]).toMatchObject({
      title: "/",
      status: "complete",
      before_url: "home-before.png",
      after_url: "home-after.png",
      interaction: true,
      layout_warnings: [],
    })
  })

  it("shows a scenario's dark pair only when both sides have one", () => {
    const scenarios = StoredCapture.read({
      key: "pulls/7/latest",
      updatedAt: null,
      document: {
        report: {
          scenarios: [
            {
              id: "home",
              before_url: "home-before.png",
              after_url: "home-after.png",
              before_dark_url: "home-before-dark.png",
              after_dark_url: "home-after-dark.png",
            },
            {
              id: "menu",
              before_url: "menu-before.png",
              after_url: "menu-after.png",
              before_dark_url: "menu-before-dark.png",
            },
          ],
        },
      },
    }).report?.scenarios
    const [home, menu] = scenarios ?? []
    if (!home || !menu) throw new Error("Both scenarios should read")

    expect(StoredCapture.shots(home, "dark")).toEqual({
      before: "home-before-dark.png",
      after: "home-after-dark.png",
      theme: "dark",
    })
    expect(StoredCapture.shots(menu, "dark")).toEqual({
      before: "menu-before.png",
      after: "menu-after.png",
      theme: "light",
    })
  })

  it("reads a capture that never reported, such as a blocked one, without a report", () => {
    const capture = StoredCapture.read({
      key: "pulls/7/latest",
      updatedAt: "2026-11-09T10:05:00Z",
      document: { status: "blocked", error: "The preview would not load." },
    })

    expect(capture).toMatchObject({
      status: "blocked",
      error: "The preview would not load.",
      report: null,
    })
  })

  it("reads a UUIDv7's time and nothing from other ids", () => {
    expect(
      StoredCapture.createdAt("019a6a5c-7e00-7abc-8def-0123456789ab")
    ).toBe(new Date(0x019a6a5c7e00).toISOString())
    expect(
      StoredCapture.createdAt("0190b000-0000-4000-8000-000000000001")
    ).toBeNull()
    expect(StoredCapture.createdAt("latest")).toBeNull()
  })
})

describe("StoredCapture.read assessment", () => {
  const read = (report: Record<string, unknown>) =>
    StoredCapture.read({
      key: "pulls/7/019a6a5c-7e00-7abc-8def-0123456789ab",
      updatedAt: null,
      document: {
        report: { summary: "- Tabs restyled", scenarios: [], ...report },
      },
    }).report

  it("reads the risk, the design system check, each note's severity and missed surfaces", () => {
    const report = read({
      coverage: {
        surfaces: ["Filters", "App header on home"],
        missed: [
          {
            title: "App header on home",
            path: "/",
            reason: "Shown only behind a flag.",
          },
          { path: "/settings" },
          { reason: "Neither a title nor a path." },
        ],
      },
      risk: { score: 4, reason: "Hand-rolled dropdown replaces Macaw Select." },
      design_system: {
        sources: ["smith-frontend/DESIGN.md", "Macaw docs"],
        findings: [
          {
            severity: "error",
            title: "Hand-rolled dropdown",
            rule: "Macaw · Select",
            location: "src/Filters.tsx:42",
            scenario: "filters",
          },
          { title: "Spacing off-scale", severity: "bogus" },
          { severity: "error" },
        ],
      },
      scenarios: [
        {
          id: "filters",
          annotations: [
            {
              x: 1,
              y: 2,
              width: 3,
              height: 4,
              title: "Dropdown",
              severity: "error",
            },
            { x: 1, y: 2, width: 3, height: 4, title: "Gap" },
          ],
        },
      ],
    })

    expect(report?.risk).toEqual({
      score: 4,
      reason: "Hand-rolled dropdown replaces Macaw Select.",
    })
    expect(report?.design_system?.sources).toEqual([
      "smith-frontend/DESIGN.md",
      "Macaw docs",
    ])
    expect(report?.design_system?.findings).toEqual([
      {
        severity: "error",
        title: "Hand-rolled dropdown",
        body: "",
        rule: "Macaw · Select",
        location: "src/Filters.tsx:42",
        scenario: "filters",
      },
      {
        severity: "warning",
        title: "Spacing off-scale",
        body: "",
        rule: "",
        location: null,
        scenario: null,
      },
    ])
    expect(
      report?.scenarios[0]?.annotations.map(({ severity }) => severity)
    ).toEqual(["error", "info"])
    expect(report?.missed).toEqual([
      {
        title: "App header on home",
        path: "/",
        reason: "Shown only behind a flag.",
      },
      { title: "/settings", path: "/settings", reason: "" },
    ])
  })

  it("leaves captures saved before reviews scored them unscored", () => {
    const report = read({ risk: { score: 9 } })
    expect(report?.risk).toBeNull()
    expect(report?.design_system).toBeNull()
    expect(report?.missed).toEqual([])
  })
})

describe("CaptureAttempt", () => {
  const attempt = (status: "blocked" | "failed" | "skipped", reason: string) =>
    new CaptureAttempt(
      {
        id: "attempt",
        head_sha: "b",
        created_at: "2026-11-09T10:00:00Z",
        status,
        reason,
      },
      Date.parse("2026-11-09T10:10:00Z")
    )

  it("keeps known deploying previews distinct from actual blockers and failures", () => {
    expect(
      attempt("blocked", "Preview pending: deployment is building.")
        .previewPending
    ).toBe(true)
    expect(
      attempt("blocked", "The preview is still building.").previewPending
    ).toBe(true)
    expect(
      attempt("blocked", "The preview is not deployed yet.").previewPending
    ).toBe(true)
    expect(attempt("blocked", "Preview build failed.").previewPending).toBe(
      false
    )
    expect(
      attempt(
        "blocked",
        "The preview is still building but authentication failed."
      ).previewPending
    ).toBe(false)
    expect(attempt("blocked", "Sign-in required.").label).toBe(
      "Capture blocked"
    )
    expect(
      attempt("failed", "Preview pending: screenshot save failed.").label
    ).toBe("Capture failed")
  })

  it("restores manual recovery when an old preview wait may have expired", () => {
    const pending = attempt(
      "blocked",
      "Preview pending: deployment is building."
    )
    const expired = new CaptureAttempt(
      pending.outcome,
      Date.parse("2026-11-09T12:00:00Z")
    )
    expect(expired.previewPending).toBe(false)
    expect(expired.label).toBe("Preview status needs refresh")
  })

  it("hides only confirmed nonvisual skips and treats legacy missing evidence as unavailable", () => {
    expect(
      attempt(
        "skipped",
        "The pull request changes no file this app reviews, so Robin skipped it."
      ).nonvisual
    ).toBe(true)
    expect(
      attempt("skipped", "No visual changes: internal refactor only.").nonvisual
    ).toBe(true)
    expect(
      attempt("skipped", "No frontend files have a preview available.")
        .nonvisual
    ).toBe(false)
    expect(
      attempt("skipped", "Changed frontend has no preview.").unavailable
    ).toBe(true)
    expect(attempt("skipped", "No base comparison configured.").label).toBe(
      "Comparison unavailable"
    )
    expect(
      attempt("blocked", "Comparison unavailable: configure a base.")
        .unavailable
    ).toBe(false)
  })
})
