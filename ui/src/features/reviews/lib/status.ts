import type { OpenPullRequest } from "@/lib/api"

export function pullRequestKey(pr: { repo: string; number: number }) {
  return `${pr.repo}#${pr.number}`
}

/** One pill per condition the PR is in; Reviewable only when none applies. */
export function statusLabels(pr: OpenPullRequest): string[] {
  if (pr.detailsLoading) return ["Loading…"]
  if (pr.detailsError) return ["Could not load status"]
  const labels = [
    ...(pr.draft ? ["Draft"] : []),
    ...(pr.mergeable === false || pr.mergeState === "dirty"
      ? ["Conflicted"]
      : []),
    ...(pr.ci === "failing"
      ? ["Failing"]
      : pr.ci === "pending"
        ? ["Pending"]
        : []),
    // Nothing known to be wrong is not the same as known to be fine.
    ...(!pr.statusAvailable ||
    (pr.ci === "unknown" && pr.reviewDecision === null)
      ? ["Status unavailable"]
      : []),
    ...(pr.reviewDecision === "changes_requested"
      ? ["Changes Requested"]
      : pr.reviewDecision === "approved"
        ? ["Approved"]
        : []),
    ...(pr.reviewRequired && !pr.draft ? ["Review required"] : []),
  ]
  return labels.length ? labels : ["Reviewable"]
}

/** What a compact row adds under its pills: the specifics no pill names. */
export function statusDetail(pr: OpenPullRequest): string | null {
  if (pr.detailsLoading || pr.detailsError) return null
  if (pr.failingChecks.length)
    return pr.failingChecks.length === 1
      ? `${pr.failingChecks[0]} failing`
      : `${pr.failingChecks.length} checks failing`
  if (pr.missingChecks.length)
    return pr.missingChecks.length === 1
      ? `${pr.missingChecks[0]} not reported`
      : `${pr.missingChecks.length} required checks not reported`
  if (pr.unresolvedThreads !== null && pr.unresolvedThreads > 0)
    return pr.unresolvedThreads === 1
      ? "1 unresolved comment"
      : `${pr.unresolvedThreads} unresolved comments`
  if (pr.pendingChecks.length)
    return pr.pendingChecks.length === 1
      ? `${pr.pendingChecks[0]} running`
      : `${pr.pendingChecks.length} checks running`
  return null
}

export const statusTones: Record<string, string> = {
  Conflicted: "border-risk/30 bg-risk-bg text-risk",
  Failing: "border-risk/30 bg-risk-bg text-risk",
  "Changes Requested":
    "border-risk/30 bg-risk-bg text-risk",
  Approved:
    "border-positive/30 bg-positive-bg text-positive",
  Pending:
    "border-attention/30 bg-attention-bg text-attention",
  "Review required":
    "border-attention/30 bg-attention-bg text-attention",
  "Status unavailable":
    "border-attention/30 bg-attention-bg text-attention",
  Reviewable: "border-info/30 bg-info-bg text-info",
}

export function isConflicted(pr: OpenPullRequest) {
  return pr.mergeable === false || pr.mergeState === "dirty"
}

export function hasFailingChecks(pr: OpenPullRequest) {
  return pr.ci === "failing"
}

// An unreadable count must not hide the action: the conversations may well be
// there.
export function hasUnresolvedConversations(pr: OpenPullRequest) {
  return pr.unresolvedThreads === null || pr.unresolvedThreads > 0
}

// A conflicted branch cannot be updated by GitHub; the Fix action covers it.
export function canUpdateBranch(pr: OpenPullRequest) {
  return (
    Boolean(pr.headSha) && pr.mergeable !== false && pr.mergeState !== "dirty"
  )
}

// Offer the merge unless GitHub has already refused it. It enforces rules the
// dashboard cannot see — an unresolved conversation, say — so an attempt that
// only might fail is still worth offering, and its refusal is the answer. A
// conflict, a draft, a missing required approval, or a required check that
// never reported is not a guess: those merges are certain to be rejected.
export function canAttemptMerge(pr: OpenPullRequest) {
  if (pr.draft === true || pr.reviewRequired || pr.missingChecks.length)
    return false
  return pr.mergeable !== false && pr.mergeState !== "dirty"
}
