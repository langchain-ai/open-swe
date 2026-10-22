import { useMemo } from "react"

import type { OpenPullRequest } from "@/lib/api"
import { ReviewPage } from "@/features/reviews/page/ReviewPage"

/** The review page in its inline layout, not a separate PR implementation. */
export function PullRequestDetail({
  pr,
  onClose,
}: {
  pr: OpenPullRequest
  onClose: () => void
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
      <ReviewPage pr={ref} onClose={onClose} />
    </aside>
  )
}
