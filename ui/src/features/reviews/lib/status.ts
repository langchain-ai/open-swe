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
  if (status !== "Draft") return [status]
  return [
    status,
    ...(pr.mergeable === false || pr.mergeState === "dirty"
      ? ["Conflicted"]
      : []),
    ...(pr.ci === "failing" ? ["Failing"] : []),
  ]
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

// Offer the merge unless GitHub has already refused it. It enforces rules the
// dashboard cannot see — an unresolved conversation, say — so an attempt that
// only might fail is still worth offering, and its refusal is the answer. A
// conflict or a draft is not a guess: those merges are certain to be rejected.
export function canAttemptMerge(pr: OpenPullRequest) {
  if (pr.draft === true) return false
  return pr.mergeable !== false && pr.mergeState !== "dirty"
}
