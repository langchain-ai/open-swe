/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it } from "vitest"

import { FrontendReportView } from "./FrontendReviewer"
import type { FrontendReviewRun } from "./api"

const run: FrontendReviewRun = {
  id: "00000000-0000-0000-0000-000000000001",
  status: "blocked",
  auth_mode: "dummy",
  base_url: "https://base.example.com",
  head_url: "https://pr.example.com",
  paths: ["/projects", "/settings"],
  base_sha: "a".repeat(40),
  head_sha: "b".repeat(40),
  created_at: "2026-10-26T00:00:00Z",
  updated_at: "2026-10-26T00:00:00Z",
  error: "Dummy authentication is not configured.",
  report: {
    summary: "Authentication blocked",
    ai_review_status: "not_run",
    build_verification: "unverified",
    video_status: "unavailable",
    scenarios: [],
  },
}

afterEach(cleanup)

it("does not present dummy authentication as captured coverage or AI review", () => {
  render(<FrontendReportView run={run} />)
  expect(screen.getByText("Coverage: 0/2 routes captured")).toBeTruthy()
  expect(screen.getByText("AI review: Not run")).toBeTruthy()
  expect(
    screen.getByText(/No preview was contacted and no page was reviewed/)
  ).toBeTruthy()
  expect(screen.queryByRole("img")).toBeNull()
})

it("does not count a scenario marked complete without its paired screenshots", () => {
  render(
    <FrontendReportView
      run={{
        ...run,
        status: "complete",
        auth_mode: "none",
        report: {
          ...run.report!,
          scenarios: [
            {
              id: "page",
              title: "Projects",
              path: "/projects",
              status: "complete",
              before_url: null,
              after_url: null,
              diff_url: null,
              mask_url: null,
              before_video_url: null,
              after_video_url: null,
              width: null,
              height: null,
              metrics: null,
              regions: [],
              annotations: [],
              mask_rects: [],
              tile_size: 32,
              min_region_pixels: 4,
              error: null,
            },
          ],
        },
      }}
    />
  )
  expect(screen.getByText("Coverage: 0/2 routes captured")).toBeTruthy()
  expect(screen.getByText(/does not count as visually reviewed/)).toBeTruthy()
})

it("warns when a saved report covers a different PR commit and retains the report", () => {
  const completed = {
    ...run,
    status: "complete" as const,
    auth_mode: "none" as const,
  }
  const { rerender } = render(
    <FrontendReportView run={completed} currentHeadSha={"c".repeat(40)} />
  )
  expect(screen.getByText("New PR changes have not been reviewed")).toBeTruthy()
  expect(screen.getByText(/The current PR commit is/)).toBeTruthy()
  expect(screen.getByText("Review evidence")).toBeTruthy()

  rerender(<FrontendReportView run={completed} currentHeadSha={run.head_sha} />)
  expect(screen.queryByText("New PR changes have not been reviewed")).toBeNull()
  expect(screen.getByText("Review evidence")).toBeTruthy()
})
