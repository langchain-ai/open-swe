import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import {
  PullRequestActions,
  type PullRequestOutcome,
} from "@/features/reviews/components/PullRequestActions"
import { StatusPill } from "@/features/reviews/components/StatusPill"
import { statusLabels } from "@/features/reviews/lib/status"
import { usePullRequestStatus } from "@/features/reviews/lib/usePullRequestStatus"
import { useSession } from "@/lib/session"

/** The PR list's status pills and action row (mark ready, update branch, merge, close…) on the review page. */
export function ReviewPageActions({
  owner,
  repo,
  number,
}: {
  owner: string
  repo: string
  number: number
}) {
  const session = useSession()
  const queryClient = useQueryClient()
  const [outcome, setOutcome] = useState<PullRequestOutcome>()
  const pr = usePullRequestStatus(`${owner}/${repo}`, number)
  const refreshPage = () => {
    void queryClient.invalidateQueries({
      queryKey: ["review", owner, repo, number],
    })
    void pr.refetch()
  }
  if (!session.data || !pr.data) return null
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-1">
        {statusLabels(pr.data).map((status) => (
          <StatusPill key={status} status={status} />
        ))}
      </div>
      <PullRequestActions
        pr={pr.data}
        login={session.data.login}
        outcome={outcome}
        onSettled={setOutcome}
        onSettledConfirmed={refreshPage}
        onReady={refreshPage}
        onReviewPage
      />
    </div>
  )
}
