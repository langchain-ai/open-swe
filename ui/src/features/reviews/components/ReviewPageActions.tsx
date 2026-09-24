import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import {
  PullRequestActions,
  type PullRequestOutcome,
} from "@/features/reviews/components/PullRequestActions"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

/** The PR list's action row (mark ready, update branch, merge, close…) on the review page. */
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
  const fullName = `${owner}/${repo}`
  const [outcome, setOutcome] = useState<PullRequestOutcome>()
  const pr = useQuery({
    queryKey: ["review-page-pr", fullName, number],
    queryFn: () => api.myPullRequestDetails(fullName, number),
    enabled: !!session.data,
    refetchOnWindowFocus: false,
  })
  const refreshPage = () => {
    void queryClient.invalidateQueries({
      queryKey: ["review", owner, repo, number],
    })
    void pr.refetch()
  }
  if (!session.data || !pr.data) return null
  return (
    <div className="mt-3">
      <PullRequestActions
        pr={pr.data}
        login={session.data.login}
        outcome={outcome}
        onSettled={(settled) => {
          setOutcome(settled)
          refreshPage()
        }}
        onReady={refreshPage}
      />
    </div>
  )
}
