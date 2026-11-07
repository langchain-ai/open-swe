import type {
  OpenPullRequest,
  PullRequestReviewer,
  PullRequestReviewEvent,
} from "@/lib/api"
import { sameLogin } from "@/features/reviews/lib/logins"

const SUBMITTED: Record<PullRequestReviewEvent, PullRequestReviewer["state"]> =
  {
    APPROVE: "approved",
    REQUEST_CHANGES: "changes_requested",
    COMMENT: "commented",
  }

export function standingReview(
  status: OpenPullRequest | null | undefined,
  login: string | undefined
): PullRequestReviewer["state"] | undefined {
  if (!login) return undefined
  return status?.reviewers?.find((reviewer) => sameLogin(reviewer.login, login))
    ?.state
}

function reviewDecision(
  reviewers: PullRequestReviewer[]
): OpenPullRequest["reviewDecision"] {
  const states = new Set(reviewers.map((reviewer) => reviewer.state))
  if (states.has("changes_requested")) return "changes_requested"
  return states.has("approved") ? "approved" : "none"
}

/** The status GitHub will report once `reviewer` submits `event`; mirrors `PullRequestReviewer.latest`. */
export function withSubmittedReview(
  status: OpenPullRequest | null,
  reviewer: Pick<PullRequestReviewer, "login" | "avatarUrl">,
  event: PullRequestReviewEvent
): OpenPullRequest | null {
  if (!status?.reviewers) return status
  const submitted = SUBMITTED[event]
  const previous = standingReview(status, reviewer.login)
  const state =
    submitted === "commented" && previous && previous !== "commented"
      ? previous
      : submitted
  const reviewers = [
    ...status.reviewers.filter((r) => !sameLogin(r.login, reviewer.login)),
    { ...reviewer, state },
  ]
  return { ...status, reviewers, reviewDecision: reviewDecision(reviewers) }
}

/** Whether a To Review row still waits on `login`; an open GitHub request outlasts an earlier change request. */
export function awaitsViewer(row: OpenPullRequest, login: string): boolean {
  if (row.state !== "open") return false
  if (row.assignment === "draft") return row.draft !== false
  const ready = row.draft !== true
  const review = standingReview(row, login)
  if (row.assignment === "requested") return ready && review !== "approved"
  if (row.assignment === "speculative" && !ready) return false
  return review !== "approved" && review !== "changes_requested"
}
