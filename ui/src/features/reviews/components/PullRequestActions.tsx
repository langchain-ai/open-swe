import type { OpenPullRequest } from "@/lib/api"
import { PullRequestLinks } from "../PullRequestLinks"
import {
  canAttemptMerge,
  hasUnresolvedConversations,
  isFixable,
} from "../lib/status"
import { ClosePullRequest } from "./ClosePullRequest"
import { MarkPullRequestReady } from "./MarkPullRequestReady"
import { MergePullRequest } from "./MergePullRequest"
import { PullRequestThreadAction } from "./PullRequestThreadAction"

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
  onReady,
}: {
  pr: OpenPullRequest
  login: string
  outcome?: PullRequestOutcome
  onSettled: (outcome: PullRequestOutcome) => void
  onReady: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        {outcome ? (
          <span className="text-xs text-muted-foreground">
            {outcomeLabels[outcome]} · leaves the list on the next refresh
          </span>
        ) : (
          <>
            {isFixable(pr) && (
              <PullRequestThreadAction pr={pr} login={login} action="fix" />
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
            {canAttemptMerge(pr) && (
              <MergePullRequest pr={pr} onMerged={() => onSettled("merged")} />
            )}
            <ClosePullRequest pr={pr} onClosed={() => onSettled("closed")} />
          </>
        )}
      </div>
      <PullRequestLinks repo={pr.repo} number={pr.number} title={pr.title} />
    </div>
  )
}
