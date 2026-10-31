import {
  CaretLeftIcon,
  CaretRightIcon,
  XIcon,
} from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate, useSearch } from "@tanstack/react-router"
import { useEffect } from "react"
import { toast } from "sonner"

import { pullRequestKey } from "@/features/reviews/lib/status"
import { parsePullRequestSelection } from "@/features/reviews/search"
import { warmReviewPage, type PullRequestRef } from "./queries"

const REVIEW_ROUTE = "/agents/reviews/$owner/$repo/$number"

export interface ReviewQueue {
  position: number
  total: number
  previous: PullRequestRef | null
  next: PullRequestRef | null
  open: (target: PullRequestRef) => void
  /** Moves on to the next pull request, or back to the list after the last one. */
  advance: () => void
  exit: () => void
}

/** Where this page sits in the reviews the person is working through one by one, if they are. */
export function useReviewQueue(pr: PullRequestRef): ReviewQueue | null {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const queue = useSearch({
    from: REVIEW_ROUTE,
    shouldThrow: false,
    select: (search) => search.queue,
  })
  const current = pullRequestKey({
    repo: `${pr.owner}/${pr.repo}`,
    number: pr.number,
  }).toLowerCase()
  const index = queue?.findIndex((key) => key.toLowerCase() === current) ?? -1
  const nextKey = index < 0 ? undefined : queue?.[index + 1]
  const previous = parsePullRequestSelection(queue?.[index - 1])
  const next = parsePullRequestSelection(nextKey)
  useEffect(() => {
    const upcoming = parsePullRequestSelection(nextKey)
    if (upcoming) warmReviewPage(queryClient, upcoming)
  }, [queryClient, nextKey])
  if (!queue || index < 0) return null
  const open = (target: PullRequestRef) =>
    void navigate({
      to: REVIEW_ROUTE,
      params: {
        owner: target.owner,
        repo: target.repo,
        number: String(target.number),
      },
      search: { queue },
    })
  const exit = () =>
    void navigate({ to: "/agents/reviews", search: { tab: "to-review" } })
  return {
    position: index + 1,
    total: queue.length,
    previous,
    next,
    open,
    advance: () => {
      if (next) return open(next)
      toast.success("You’ve worked through all your reviews")
      exit()
    },
    exit,
  }
}

/** The header's "2 of 7" stepper for reviews worked through one by one. */
export function ReviewQueueControls({ queue }: { queue: ReviewQueue }) {
  const { previous, next } = queue
  return (
    <div
      role="group"
      aria-label="Review queue"
      className="flex shrink-0 items-center gap-space-1 text-xs text-secondary tabular-nums"
    >
      <IconButton
        icon={CaretLeftIcon}
        label="Previous pull request"
        size="sm"
        color="secondary"
        variant="plain"
        disabled={!previous}
        onClick={() => previous && queue.open(previous)}
      />
      <span aria-live="polite">
        {queue.position} of {queue.total}
      </span>
      <IconButton
        icon={CaretRightIcon}
        label={next ? "Skip to the next pull request" : "Finish reviewing"}
        size="sm"
        color="secondary"
        variant="plain"
        onClick={queue.advance}
      />
      <IconButton
        icon={XIcon}
        label="Stop reviewing one by one"
        size="sm"
        color="secondary"
        variant="plain"
        onClick={queue.exit}
      />
    </div>
  )
}
