import { describe, expect, it } from "vitest"

import {
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
              metrics: { changed_pixels: 3, total_pixels: 12 },
              regions: [{ x: 0, y: 0, width: 2, height: 2, pixels: 3 }, "junk"],
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
      metrics: {
        changed_pixels: 3,
        total_pixels: 12,
        changed_ratio: 0.25,
        threshold: 0,
      },
      regions: [{ x: 0, y: 0, width: 2, height: 2, pixels: 3 }],
      interaction: true,
      layout_warnings: [],
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
