import type { OpenPullRequest, ReviewDetail } from "@/lib/api"
import type { PullRequestThreadActionName } from "@/features/reviews/lib/threadActions"
import {
  canAttemptMerge,
  hasFailingChecks,
  isConflicted,
} from "@/features/reviews/lib/status"

export type StandingTone =
  | "ready"
  | "blocked"
  | "waiting"
  | "merged"
  | "closed"
  | "unknown"

export type NextStep =
  | { kind: "merge" }
  | { kind: "review" }
  | { kind: "mark-ready" }
  | { kind: "request-review" }
  | { kind: "update-branch" }
  | { kind: "agent"; action: PullRequestThreadActionName }

export interface Standing {
  tone: StandingTone
  headline: string
  details: Array<string>
  next: NextStep | null
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

export function openBugCount(detail: ReviewDetail): number {
  return detail.findings.filter(
    (finding) => finding.group === "bug" && finding.status === "open"
  ).length
}

/** One sentence on where the pull request stands, and the single thing to do next. */
export function pullRequestStanding(
  detail: ReviewDetail,
  status: OpenPullRequest | null | undefined,
  viewer: string | undefined
): Standing {
  if (detail.pr.merged_at)
    return { tone: "merged", headline: "Merged.", details: [], next: null }
  if (detail.pr.state === "closed")
    return {
      tone: "closed",
      headline: "Closed without merging.",
      details: [],
      next: null,
    }
  const details: Array<string> = []
  const bugs = openBugCount(detail)
  if (bugs > 0) details.push(`Open SWE flagged ${plural(bugs, "bug")}.`)
  if (!status)
    return {
      tone: "unknown",
      headline: "Checking where this stands…",
      details,
      next: null,
    }
  const isAuthor =
    !!viewer && detail.pr.author?.login.toLowerCase() === viewer.toLowerCase()
  if (status.unresolvedThreads)
    details.push(`${plural(status.unresolvedThreads, "unresolved thread")}.`)

  if (status.draft)
    return {
      tone: "waiting",
      headline: "Still a draft.",
      details,
      next: isAuthor ? { kind: "mark-ready" } : null,
    }
  if (isConflicted(status))
    return {
      tone: "blocked",
      headline: `Conflicts with ${detail.pr.base_ref}.`,
      details,
      next: { kind: "agent", action: "fix-conflicts" },
    }
  if (hasFailingChecks(status))
    return {
      tone: "blocked",
      headline: `${plural(status.failingChecks.length || 1, "check")} failing.`,
      details,
      next: { kind: "agent", action: "fix-checks" },
    }
  if (status.reviewDecision === "changes_requested")
    return {
      tone: "blocked",
      headline: "Changes requested.",
      details,
      next: isAuthor
        ? { kind: "agent", action: "address-comments" }
        : { kind: "review" },
    }
  if (status.missingChecks.length)
    return {
      tone: "blocked",
      headline: `${plural(status.missingChecks.length, "required check")} never reported.`,
      details,
      next: null,
    }
  if (status.reviewRequired)
    return {
      tone: "waiting",
      headline: "Waiting on a review.",
      details,
      next: isAuthor ? { kind: "request-review" } : { kind: "review" },
    }
  if (status.ci === "pending")
    return {
      tone: "waiting",
      headline: "Checks are running.",
      details,
      next: isAuthor ? null : { kind: "review" },
    }
  if (status.mergeState === "behind")
    return {
      tone: "waiting",
      headline: `Behind ${detail.pr.base_ref}.`,
      details,
      next: { kind: "update-branch" },
    }
  if (canAttemptMerge(status))
    return {
      tone: "ready",
      headline:
        status.reviewDecision === "approved" ? "Approved and ready to merge." : "Ready to merge.",
      details,
      next: { kind: "merge" },
    }
  return { tone: "unknown", headline: "Open.", details, next: null }
}
