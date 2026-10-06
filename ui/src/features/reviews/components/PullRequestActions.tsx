import type { OpenPullRequest } from "@/lib/api"
import { PullRequestLinks } from "../PullRequestLinks"
import {
  canAttemptMerge,
  canUpdateBranch,
  hasFailingChecks,
  hasUnresolvedConversations,
  isConflicted,
} from "../lib/status"
import { ClosePullRequest } from "./ClosePullRequest"
import { MarkPullRequestReady } from "./MarkPullRequestReady"
import { MergePullRequest } from "./MergePullRequest"
import { PullRequestThreadAction } from "./PullRequestThreadAction"
import { RequestHumanReview } from "./RequestHumanReview"
import { UpdatePullRequestBranch } from "./UpdatePullRequestBranch"

export type PullRequestOutcome = "merged" | "closed"

const outcomeLabels: Record<PullRequestOutcome, string> = {
  merged: "Merged",
  closed: "Closed",
}

/** Everything that can be done to a PR, shared by the card and the preview. */
export function PullRequestActions({
  pr,
  login,
  outcome,
  onSettled,
  onSettledConfirmed,
  onReady,
  onReviewPage = false,
}: {
  pr: OpenPullRequest
  login: string
  outcome?: PullRequestOutcome
  onSettled: (outcome: PullRequestOutcome | undefined) => void
  /** Runs once GitHub confirms a merge or close; the list leaves it unset to keep the row. */
  onSettledConfirmed?: () => void
  onReady: () => void
  onReviewPage?: boolean
}) {
  const settle = (next: PullRequestOutcome) => {
    onSettled(next)
    return () => onSettled(undefined)
  }
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {outcome && (
        <span className="text-meta text-ink-subtle">
          {outcomeLabels[outcome]} · leaves the list on the next refresh
        </span>
      )}
      {/* Hidden rather than unmounted, so a rolled-back outcome keeps each control's state. */}
      <div
        hidden={Boolean(outcome)}
        className="flex flex-wrap items-center gap-x-2 gap-y-2"
      >
        {isConflicted(pr) && (
          <PullRequestThreadAction
            pr={pr}
            login={login}
            action="fix-conflicts"
          />
        )}
        {hasFailingChecks(pr) && (
          <PullRequestThreadAction pr={pr} login={login} action="fix-checks" />
        )}
        {hasUnresolvedConversations(pr) && (
          <PullRequestThreadAction
            pr={pr}
            login={login}
            action="address-comments"
          />
        )}
        {pr.draft === true && (
          <MarkPullRequestReady pr={pr} onReady={onReady} />
        )}
        {pr.draft === false && <RequestHumanReview pr={pr} />}
        {canUpdateBranch(pr) && (
          <UpdatePullRequestBranch pr={pr} onUpdated={onReady} />
        )}
        {canAttemptMerge(pr) && (
          <MergePullRequest
            pr={pr}
            apply={() => settle("merged")}
            onMerged={onSettledConfirmed}
          />
        )}
        {pr.missingChecks.length > 0 && (
          <span className="text-label text-attention">
            Merge blocked: {pr.missingChecks.join(", ")} never reported
          </span>
        )}
        <ClosePullRequest
          pr={pr}
          apply={() => settle("closed")}
          onClosed={onSettledConfirmed}
        />
      </div>
      <PullRequestLinks
        repo={pr.repo}
        number={pr.number}
        title={pr.title}
        onReviewPage={onReviewPage}
      />
    </div>
  )
}
