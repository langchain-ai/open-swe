import type { MouseEvent as ReactMouseEvent, ReactNode } from "react"

import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"

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

// A portaled menu, popover or dialog still bubbles its React events through
// the card, so those count as the card's controls too.
const interactive =
  'a,button,input,select,textarea,[role="button"],[role="menu"],[role="dialog"],[role="alertdialog"]'

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
    const detail = statusDetail(pr)
    return (
      <Stack
        render={
          <button
            type="button"
            aria-current={selected ? "true" : undefined}
            onClick={onSelect}
          />
        }
        gap="xs"
        radius="compact"
        className={cn(
          "w-full px-3 py-2 text-left",
          selected ? "bg-selected" : "hover:bg-hover",
          outcome && "opacity-60"
        )}
      >
        <Inline gap="sm" align="baseline" className="text-meta text-ink-subtle">
          <span className="min-w-0 truncate">{pr.repo}</span>
          <span className="font-mono tabular-nums">#{pr.number}</span>
        </Inline>
        <Box
          render={<span />}
          className="truncate text-label font-medium text-ink"
        >
          {pr.title}
        </Box>
        {outcome ? (
          <span className="truncate text-meta text-ink-subtle">
            {outcomeLabels[outcome]}
          </span>
        ) : (
          <>
            <Inline gap="xs" wrap>
              {statusLabels(pr).map((status) => (
                <StatusPill key={status} status={status} />
              ))}
            </Inline>
            {detail && (
              <span className="truncate text-meta text-ink-subtle">
                {detail}
              </span>
            )}
          </>
        )}
      </Stack>
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
    <Stack
      onClick={openFromCard}
      gap="md"
      bg={selected ? "selected" : "panel"}
      border={selected ? "line-strong" : "line"}
      radius="panel"
      padding="lg"
      className={cn(
        "min-w-0 cursor-pointer hover:border-line-strong",
        outcome && "opacity-60"
      )}
    >
      <Stack gap="xs" className="min-w-0">
        <Inline gap="sm" wrap className="text-meta text-ink-subtle">
          <span>
            {pr.repo}{" "}
            <span className="font-mono tabular-nums">#{pr.number}</span>
          </span>
          {statusLabels(pr).map((status) => (
            <StatusPill key={status} status={status} />
          ))}
        </Inline>
        <Box
          render={<h3 />}
          className="text-body font-medium break-words text-ink"
        >
          <button
            type="button"
            className="text-left hover:underline"
            onClick={onSelect}
          >
            {pr.title}
          </button>
        </Box>
      </Stack>
      <Inline gap="lg" align="start" wrap className="text-meta text-ink-subtle">
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
          <span className="text-attention">Live PR status unavailable</span>
        )}
      </Inline>
      <PullRequestChecks pr={pr} />
      <PullRequestActions
        pr={pr}
        login={login}
        outcome={outcome}
        onSettled={onSettled}
        onReady={onReady}
      />
    </Stack>
  )
}
