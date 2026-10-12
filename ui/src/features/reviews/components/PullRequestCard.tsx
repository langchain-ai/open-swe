import { Text } from "@langchain/macaw-components/Text"
import type { MouseEvent as ReactMouseEvent, ReactNode } from "react"

import type { OpenPullRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import { dateLabel } from "../lib/dateLabel"
import { statusDetail, statusLabels } from "../lib/status"
import { Diffstat } from "./Diffstat"
import {
  PullRequestActions,
  type PullRequestOutcome,
} from "./PullRequestActions"
import { PullRequestChecks } from "./PullRequestChecks"
import { StatusPill } from "./StatusPill"
import { UnresolvedConversations } from "./UnresolvedConversations"

export type { PullRequestOutcome }

const outcomeLabels: Record<PullRequestOutcome, string> = {
  merged: "Merged",
  closed: "Closed",
}

const interactive =
  'a,button,input,select,textarea,[role="button"],[role="menu"]'

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
  onSettled: (outcome: PullRequestOutcome | undefined) => void
  onReady: () => void
}) {
  if (compact) {
    return (
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className={cn(
          "flex w-full gap-space-3 rounded-md py-space-2 pr-space-2 pl-space-2 text-left transition-colors",
          selected ? "bg-selected" : "hover:bg-surface-level-1-hover",
          outcome && "opacity-60"
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 w-0.5 shrink-0 self-stretch rounded-full",
            selected ? "bg-brand" : "bg-transparent"
          )}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-space-2 text-xs text-secondary">
            <span className="min-w-0 truncate">{pr.repo}</span>
            <span className="font-mono tabular-nums">#{pr.number}</span>
          </span>
          <span className="mt-0.5 block truncate text-xs font-medium text-primary">
            {pr.title}
          </span>
          {outcome ? (
            <span className="mt-0.5 block truncate text-xs text-secondary">
              {outcomeLabels[outcome]}
            </span>
          ) : (
            <>
              <span className="mt-space-1 flex flex-wrap gap-space-1">
                {statusLabels(pr).map((status) => (
                  <StatusPill key={status} status={status} />
                ))}
              </span>
              {statusDetail(pr) && (
                <span className="mt-0.5 block truncate text-xs text-secondary">
                  {statusDetail(pr)}
                </span>
              )}
            </>
          )}
        </span>
      </button>
    )
  }

  // Anywhere on the card opens the preview, but the card is full of its own
  // controls, and a click that lands on one of those means that control. The
  // title stays a real button so the card is reachable without a pointer.
  const openFromCard = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.target instanceof Element && event.target.closest(interactive))
      return
    if (window.getSelection()?.toString()) return
    onSelect()
  }

  return (
    <div
      onClick={openFromCard}
      className={cn(
        "cursor-pointer rounded-lg border bg-surface-level-1 p-space-4 transition-colors",
        selected
          ? "border-brand"
          : "border-default hover:bg-surface-level-1-hover",
        outcome && "opacity-60"
      )}
    >
      <div className="min-w-0 space-y-space-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-2 text-xs text-secondary">
            <span>
              {pr.repo}{" "}
              <span className="font-mono tabular-nums">#{pr.number}</span>
            </span>
            {statusLabels(pr).map((status) => (
              <StatusPill key={status} status={status} />
            ))}
          </div>
          <Text
            as="h3"
            variant="h5"
            weight="medium"
            className="mt-space-1 break-words"
          >
            <button
              type="button"
              className="text-left hover:underline"
              onClick={onSelect}
            >
              {pr.title}
            </button>
          </Text>
        </div>
        <div className="flex flex-wrap items-center gap-x-space-5 gap-y-space-2 text-xs text-secondary">
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
            <span className="text-warning-secondary">
              Live PR status unavailable
            </span>
          )}
        </div>
        <PullRequestChecks pr={pr} />
        <PullRequestActions
          pr={pr}
          login={login}
          outcome={outcome}
          onSettled={onSettled}
          onReady={onReady}
        />
      </div>
    </div>
  )
}
