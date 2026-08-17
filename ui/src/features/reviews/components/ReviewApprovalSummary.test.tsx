/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { ReviewApprovalAssessment } from "@/lib/api"
import { ReviewApprovalSummary } from "./ReviewApprovalSummary"

const assessment: ReviewApprovalAssessment = {
  rubric_version: "1",
  assessed_sha: "abcdef123456",
  raw_score: 95,
  score: 95,
  reasons: ["Reviewed the complete diff"],
  risks: [],
  valid: true,
  policy: { effective_threshold: 90 },
  decision: "approved",
  blockers: [],
  github_review_id: 42,
  github_review_event: "APPROVE",
  recorded_at: "2026-08-17T00:00:00Z",
  stale: false,
}

afterEach(() => cleanup())

describe("ReviewApprovalSummary", () => {
  it("renders an actual approval and score", () => {
    render(<ReviewApprovalSummary assessment={assessment} />)

    expect(screen.getByText("Approved by Open SWE")).toBeTruthy()
    expect(screen.getByText("95/100")).toBeTruthy()
    expect(screen.getByText("Reviewed the complete diff")).toBeTruthy()
  })

  it("does not present a stale assessment as approved", () => {
    render(
      <ReviewApprovalSummary
        assessment={{
          ...assessment,
          stale: true,
          blockers: ["stale_head"],
        }}
      />
    )

    expect(screen.getByText("Assessment is for an earlier commit")).toBeTruthy()
    expect(screen.getByText(/stale head/)).toBeTruthy()
  })
})
