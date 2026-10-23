import { useMemo } from "react"

import type { OpenPullRequest } from "@/lib/api"
import type { ReviewsSearch } from "../search"
import { PullRequestActions } from "./PullRequestActions"
import { ReviewPage } from "@/features/reviews/page/ReviewPage"

/** The review page in its inline layout, not a separate PR implementation. */
export function PullRequestDetail({
  pr,
  onClose,
  actions,
  position,
  onPositionChange,
}: {
  pr: OpenPullRequest
  onClose: () => void
  actions: Omit<Parameters<typeof PullRequestActions>[0], "pr">
  position: Pick<ReviewsSearch, "files" | "at">
  onPositionChange: (position: Pick<ReviewsSearch, "files" | "at">) => void
}) {
  const ref = useMemo(() => {
    const [owner, repo] = pr.repo.split("/")
    return { owner: owner!, repo: repo!, number: pr.number }
  }, [pr.repo, pr.number])
  return (
    <aside
      aria-label={`Pull request ${pr.repo} #${pr.number}`}
      className="flex min-h-0 w-full min-w-0 overflow-hidden rounded-lg border border-default bg-surface-level-1"
    >
      <ReviewPage
        pr={ref}
        onClose={onClose}
        position={position}
        onPositionChange={onPositionChange}
        actions={<PullRequestActions pr={pr} {...actions} pageLink={false} />}
      />
    </aside>
  )
}
