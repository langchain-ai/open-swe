/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it } from "vitest"
import type { PublishedReviewAssessment } from "@/lib/api"
import { ReviewAssessmentCard } from "./ReviewAssessmentCard"

const assessment: PublishedReviewAssessment = {
  review_id: 123,
  head_sha: "a".repeat(40),
  risk_score: 1,
  decision: "would_approve",
  explanation: "Only documentation changed.",
}

afterEach(cleanup)

it("shows the assessment and its explanation without asking for feedback", () => {
  window.location.hash = "#assessment-feedback"
  render(
    <ReviewAssessmentCard
      assessment={assessment}
      headSha={assessment.head_sha}
    />
  )
  expect(screen.getByText("Risk 1/5")).toBeTruthy()
  expect(screen.getByText("Would approve")).toBeTruthy()
  expect(screen.queryByRole("button")).toBeNull()
  expect(screen.queryByRole("textbox")).toBeNull()
  fireEvent.click(screen.getByText("Why this assessment?"))
  expect(screen.getByText(assessment.explanation)).toBeTruthy()
  window.location.hash = ""
})

it("warns when the assessment covers an earlier commit", () => {
  render(
    <ReviewAssessmentCard assessment={assessment} headSha={"b".repeat(40)} />
  )
  expect(
    screen.getByText("This assessment is for an earlier commit.")
  ).toBeTruthy()
})
