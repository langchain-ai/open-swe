import { describe, expect, it } from "vitest"
import type { OpenPullRequest, ReviewDetail } from "@/lib/api"
import { pullRequestStanding } from "./pullRequestStanding"

const detail = {
  pr: {
    state: "open",
    merged_at: null,
    base_ref: "main",
    author: { login: "a" },
    requested_reviewers: [],
  },
  findings: [],
} as unknown as ReviewDetail

const status = (mergeState: string) =>
  ({
    draft: false,
    mergeable: true,
    mergeState,
    ci: "passing",
    failingChecks: [],
    missingChecks: [],
    reviewRequired: false,
    reviewDecision: "approved",
    unresolvedThreads: 0,
  }) as unknown as OpenPullRequest

describe("pullRequestStanding", () => {
  it("says ready only when GitHub reports a clean merge state", () => {
    expect(
      pullRequestStanding(detail, status("clean"), "a", null).headline
    ).toBe("Approved and ready to merge.")
  })

  it("does not claim readiness while GitHub blocks the merge", () => {
    const standing = pullRequestStanding(detail, status("blocked"), "a", null)
    expect(standing.tone).toBe("blocked")
    expect(standing.headline).toBe(
      "Approved, but GitHub is blocking the merge."
    )
  })
})
