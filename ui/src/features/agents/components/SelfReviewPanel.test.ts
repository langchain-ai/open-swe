import { describe, expect, it } from "vitest"

import type { SelfReview, SelfReviewFinding } from "@/features/agents/lib/types"
import {
  findingAnchorLabel,
  selfReviewSummary,
} from "@/features/agents/components/SelfReviewPanel"

function finding(
  overrides: Partial<SelfReviewFinding> = {}
): SelfReviewFinding {
  return {
    id: "f1",
    severity: "medium",
    confidence: "medium",
    category: "correctness",
    title: "Wrong key breaks the lookup",
    description: "",
    suggestion: null,
    file: "agent/thing.py",
    start_line: 12,
    end_line: 12,
    disposition: "pending",
    disposition_note: "",
    created_at: "2026-01-01T00:00:00+00:00",
    ...overrides,
  }
}

function review(findings: Array<SelfReviewFinding>): SelfReview {
  return {
    prNumber: 7,
    prUrl: "https://github.com/langchain-ai/open-swe/pull/7",
    repoFullName: "langchain-ai/open-swe",
    headSha: "abc",
    status: "complete",
    updatedAt: "2026-01-01T00:00:00+00:00",
    findings,
  }
}

describe("findingAnchorLabel", () => {
  it("collapses a single-line range and keeps a real one", () => {
    expect(findingAnchorLabel(finding())).toBe("agent/thing.py:12")
    expect(findingAnchorLabel(finding({ end_line: 15 }))).toBe(
      "agent/thing.py:12-15"
    )
  })

  it("falls back to the file when there is no line", () => {
    expect(
      findingAnchorLabel(finding({ start_line: null, end_line: null }))
    ).toBe("agent/thing.py")
    expect(findingAnchorLabel(finding({ file: "" }))).toBe("")
  })
})

describe("selfReviewSummary", () => {
  it("counts findings by disposition", () => {
    expect(
      selfReviewSummary(
        review([
          finding({ id: "a", disposition: "fixed" }),
          finding({ id: "b", disposition: "deferred" }),
          finding({ id: "c", disposition: "dismissed" }),
          finding({ id: "d" }),
        ])
      )
    ).toBe("4 findings · 1 fixed · 1 needs your call · 1 dismissed · 1 open")
  })

  it("says so when the review found nothing", () => {
    expect(selfReviewSummary(review([]))).toBe("No findings")
  })
})
