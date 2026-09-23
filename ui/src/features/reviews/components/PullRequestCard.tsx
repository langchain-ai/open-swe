import type { MouseEvent as ReactMouseEvent, ReactNode } from "react"

import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import type { OpenPullRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import { dateLabel } from "../lib/dateLabel"
import { blockerLabel, blockerTone, statusLabels } from "../lib/status"
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

function Timestamp({ value }: { value: string | null }) {
  const label = dateLabel(value)
  if (!value) return <time>{label}</time>
  return (
    <Tooltip>
      <TooltipTrigger render={<time dateTime={value} />}>
        {label}
      </TooltipTrigger>
      <TooltipPopup>{value}</TooltipPopup>
    </Tooltip>
  )
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
        "cursor-pointer rounded-lg border bg-card p-4 transition-colors",
        selected
          ? "border-foreground/30"
          : "border-border hover:border-foreground/20",
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
            Updated <Timestamp value={pr.updatedAt} />
          </span>
          <span>
            Opened <Timestamp value={pr.createdAt} />
          </span>
          <UnresolvedConversations pr={pr} />
          {!pr.statusAvailable && !pr.detailsLoading && (
            <span className="text-warning-foreground">
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
