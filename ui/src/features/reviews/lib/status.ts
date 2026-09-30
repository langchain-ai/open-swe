import type { OpenPullRequest } from "@/lib/api"

export function pullRequestKey(pr: { repo: string; number: number }) {
  return `${pr.repo}#${pr.number}`
}

export function overallStatus(pr: OpenPullRequest) {
  if (pr.detailsLoading) return "Loading…"
  if (pr.detailsError) return "Could not load status"
  if (pr.draft) return "Draft"
  if (pr.mergeable === false || pr.mergeState === "dirty") return "Conflicted"
  if (pr.ci === "failing") return "Failing"
  if (pr.ci === "pending") return "Pending"
  if (
    !pr.statusAvailable ||
    (pr.ci === "unknown" && pr.reviewDecision === null)
  )
    return "Status unavailable"
  if (pr.reviewDecision === "changes_requested") return "Changes Requested"
  if (pr.reviewDecision === "approved") return "Approved"
  return "Reviewable"
}

// A draft still has to show a conflict or a failing check: they are what the
// author has to act on, and they outlive the draft flag.
export function statusLabels(pr: OpenPullRequest): string[] {
  const status = overallStatus(pr)
  // The missing approval is why the merge action is gone, so say so alongside
  // whatever else the PR is waiting on.
  const review = pr.reviewRequired ? ["Review required"] : []
  if (status !== "Draft") return [status, ...review]
  return [
    status,
    ...(pr.mergeable === false || pr.mergeState === "dirty"
      ? ["Conflicted"]
      : []),
    ...(pr.ci === "failing" ? ["Failing"] : []),
    ...review,
  ]
}

/**
 * The single line a compact row shows under the title: whatever is most
 * worth acting on. Ordered by how hard it blocks the merge, not by how the
 * status was derived.
 */
export function blockerLabel(pr: OpenPullRequest): string {
  if (pr.detailsLoading) return "Loading…"
  if (pr.detailsError) return "Could not load status"
  if (pr.mergeable === false || pr.mergeState === "dirty")
    return "Merge conflict"
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
  if (pr.reviewDecision === "changes_requested") return "Changes requested"
  if (pr.pendingChecks.length)
    return pr.pendingChecks.length === 1
      ? `${pr.pendingChecks[0]} running`
      : `${pr.pendingChecks.length} checks running`
  if (pr.reviewRequired) return "Waiting on review"
  if (pr.draft) return "Draft"
  // Nothing known to be wrong is not the same as known to be fine: claiming
  // readiness for a PR whose status GitHub never returned would be a lie.
  if (
    !pr.statusAvailable ||
    (pr.ci === "unknown" && pr.reviewDecision === null)
  )
    return "Status unavailable"
  if (pr.reviewDecision === "approved") return "Approved, ready to merge"
  return "Ready to merge"
}

export function blockerTone(pr: OpenPullRequest): string {
  if (
    pr.mergeable === false ||
    pr.mergeState === "dirty" ||
    pr.ci === "failing"
  )
    return "text-destructive"
  if (pr.reviewDecision === "changes_requested") return "text-destructive"
  if (
    pr.ci === "pending" ||
    pr.missingChecks.length > 0 ||
    pr.reviewRequired ||
    (pr.unresolvedThreads !== null && pr.unresolvedThreads > 0)
  )
    return "text-amber-700 dark:text-amber-400"
  if (
    !pr.statusAvailable ||
    (pr.ci === "unknown" && pr.reviewDecision === null)
  )
    return "text-amber-700 dark:text-amber-400"
  if (pr.reviewDecision === "approved")
    return "text-emerald-700 dark:text-emerald-400"
  return "text-muted-foreground"
}

export const statusTones: Record<string, string> = {
  Conflicted: "border-destructive/30 bg-destructive/10 text-destructive",
  Failing: "border-destructive/30 bg-destructive/10 text-destructive",
  "Changes Requested":
    "border-destructive/30 bg-destructive/10 text-destructive",
  Approved:
    "border-emerald-600/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  Pending:
    "border-amber-600/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  "Review required":
    "border-amber-600/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  Reviewable: "border-sky-600/30 bg-sky-500/10 text-sky-700 dark:text-sky-400",
}

export function isFixable(pr: OpenPullRequest) {
  return (
    pr.mergeable === false || pr.mergeState === "dirty" || pr.ci === "failing"
  )
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
