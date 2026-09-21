import type { ReactNode } from "react"

import type { OpenPullRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import { dateLabel } from "../lib/dateLabel"
import {
  blockerLabel,
  blockerTone,
  canAttemptMerge,
  hasUnresolvedConversations,
  isFixable,
  statusLabels,
} from "../lib/status"
import { PullRequestLinks } from "../PullRequestLinks"
import { ClosePullRequest } from "./ClosePullRequest"
import { Diffstat } from "./Diffstat"
import { MarkPullRequestReady } from "./MarkPullRequestReady"
import { MergePullRequest } from "./MergePullRequest"
import { PullRequestChecks } from "./PullRequestChecks"
import { PullRequestThreadAction } from "./PullRequestThreadAction"
import { StatusPill } from "./StatusPill"
import { UnresolvedConversations } from "./UnresolvedConversations"

export type PullRequestOutcome = "merged" | "closed"

const outcomeLabels: Record<PullRequestOutcome, string> = {
  merged: "Merged",
  closed: "Closed",
}

export function PullRequestCard({
  pr,
  login,
  review,
  outcome,
  compact,
  selected,
  onSelect,
  onSettled,
  onReady,
}: {
  pr: OpenPullRequest
  login: string
  review: ReactNode
  // Set once the PR has left GitHub's open list. The card keeps its place
  // until the next refresh so the rows below it never jump.
  outcome?: PullRequestOutcome
  // Rail form: title plus the one thing blocking the merge, and no controls —
  // those live in the preview the row opens.
  compact?: boolean
  selected?: boolean
  onSelect: () => void
  onSettled: (outcome: PullRequestOutcome) => void
  onReady: () => void
}) {
  if (compact) {
    return (
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className={cn(
          "flex w-full gap-2.5 rounded-md py-2 pr-2.5 pl-2 text-left transition-colors",
          selected ? "bg-sidebar-row-hover" : "hover:bg-sidebar-row-hover",
          outcome && "opacity-60"
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 w-0.5 shrink-0 self-stretch rounded-full",
            selected ? "bg-foreground/60" : "bg-transparent"
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-1.5 text-xs text-muted-foreground">
            <span className="min-w-0 truncate">{pr.repo}</span>
            <span className="font-mono tabular-nums">#{pr.number}</span>
          </span>
          <span className="mt-0.5 block truncate text-xs font-medium text-foreground">
            {pr.title}
          </span>
          <span
            className={cn("mt-0.5 block truncate text-xs", blockerTone(pr))}
          >
            {outcome ? outcomeLabels[outcome] : blockerLabel(pr)}
          </span>
        </span>
      </button>
    )
  }

  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-4 transition-colors",
        selected ? "border-foreground/30" : "border-border",
        outcome && "opacity-60"
      )}
    >
      <div className="min-w-0 space-y-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
            <span>
              {pr.repo}{" "}
              <span className="font-mono tabular-nums">#{pr.number}</span>
            </span>
            {statusLabels(pr).map((status) => (
              <StatusPill key={status} status={status} />
            ))}
          </div>
          <h3 className="mt-1 text-sm font-medium break-words">
            <button
              type="button"
              className="text-left hover:underline"
              onClick={onSelect}
            >
              {pr.title}
            </button>
          </h3>
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
          <Diffstat pr={pr} />
          {review}
          <span>
            Updated{" "}
            <time
              dateTime={pr.updatedAt ?? undefined}
              title={pr.updatedAt ?? undefined}
            >
              {dateLabel(pr.updatedAt)}
            </time>
          </span>
          <span>
            Opened{" "}
            <time
              dateTime={pr.createdAt ?? undefined}
              title={pr.createdAt ?? undefined}
            >
              {dateLabel(pr.createdAt)}
            </time>
          </span>
          <UnresolvedConversations pr={pr} />
          {!pr.statusAvailable && !pr.detailsLoading && (
            <span className="text-amber-700 dark:text-amber-400">
              Live PR status unavailable
            </span>
          )}
        </div>
        <PullRequestChecks pr={pr} />
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
                  <MergePullRequest
                    pr={pr}
                    onMerged={() => onSettled("merged")}
                  />
                )}
                <ClosePullRequest
                  pr={pr}
                  onClosed={() => onSettled("closed")}
                />
              </>
            )}
          </div>
          <PullRequestLinks
            repo={pr.repo}
            number={pr.number}
            title={pr.title}
          />
        </div>
      </div>
    </div>
  )
}
