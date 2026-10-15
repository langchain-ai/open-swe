import type { OpenPullRequest, ReviewDetail } from "@/lib/api"
import { sameLogin } from "@/features/reviews/lib/logins"
import {
  canAttemptMerge,
  canUpdateBranch,
  hasFailingChecks,
  isConflicted,
} from "@/features/reviews/lib/status"
import type { PullRequestThreadActionName } from "@/features/reviews/lib/threadActions"
import { openFindingCounts } from "./findings"
import { plural } from "./text"

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

/** A supporting fact, and what clicking it opens. */
export interface StandingDetail {
  text: string
  opens: "findings" | "conversations"
}

export interface Standing {
  tone: StandingTone
  headline: string
  details: Array<StandingDetail>
  next: NextStep | null
}

/** Still open on GitHub, as a draft or ready for review. */
export function isLive(detail: ReviewDetail): boolean {
  return detail.pr.state === "open" || detail.pr.state === "draft"
}

/** One sentence on where the pull request stands, and the single thing to do next. */
export function pullRequestStanding(
  detail: ReviewDetail,
  status: OpenPullRequest | null | undefined,
  viewer: string | undefined,
  open: { current: number; outdated: number } | null
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
  const details: Array<StandingDetail> = []
  const { bugs, flags } = openFindingCounts(detail.findings)
  if (bugs > 0)
    details.push({
      text: `Open SWE flagged ${plural(bugs, "bug")}`,
      opens: "findings",
    })
  else if (flags > 0)
    details.push({
      text: `Open SWE flagged ${plural(flags, "thing")} to look into`,
      opens: "findings",
    })
  if (!status)
    return {
      tone: "unknown",
      headline:
        status === undefined
          ? "Checking where this stands…"
          : "Open. GitHub didn't say whether it can merge.",
      details,
      next: null,
    }
  const isAuthor = sameLogin(detail.pr.author?.login, viewer)
  const conversations = open ?? {
    current: status.unresolvedThreads ?? 0,
    outdated: 0,
  }
  if (conversations.current)
    details.push({
      text: plural(conversations.current, "open conversation"),
      opens: "conversations",
    })
  if (conversations.outdated)
    details.push({
      text: `${plural(conversations.outdated, "outdated conversation")} still unresolved`,
      opens: "conversations",
    })

  if (status.draft)
    return {
      tone: "waiting",
      headline: "Still a draft.",
      details,
      next: { kind: "mark-ready" },
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
      next: !isAuthor
        ? { kind: "review" }
        : conversations.current > 0
          ? { kind: "agent", action: "address-comments" }
          : null,
    }
  if (status.missingChecks.length)
    return {
      tone: "blocked",
      headline: `${plural(status.missingChecks.length, "required check")} never reported.`,
      details,
      next: null,
    }
  if (status.reviewRequired) {
    const askedOfViewer = detail.pr.requested_reviewers.some((reviewer) =>
      sameLogin(reviewer.login, viewer)
    )
    return {
      tone: "waiting",
      headline: askedOfViewer
        ? "Waiting on your review."
        : "Waiting on a review.",
      details,
      next: isAuthor ? { kind: "request-review" } : { kind: "review" },
    }
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
      next: canUpdateBranch(status) ? { kind: "update-branch" } : null,
    }
  if (canAttemptMerge(status)) {
    // GitHub would merge it, but someone still has something to say about it.
    const outstanding =
      bugs > 0
        ? plural(bugs, "bug")
        : flags > 0
          ? plural(flags, "flag")
          : conversations.current > 0
            ? plural(conversations.current, "open conversation")
            : null
    const approved = status.reviewDecision === "approved"
    return {
      tone: outstanding ? "waiting" : "ready",
      headline: outstanding
        ? `${approved ? "Approved" : "Mergeable"}, with ${outstanding} still open.`
        : approved
          ? "Approved and ready to merge."
          : "Ready to merge.",
      details,
      next: { kind: "merge" },
    }
  }
  return { tone: "unknown", headline: "Open.", details, next: null }
}
