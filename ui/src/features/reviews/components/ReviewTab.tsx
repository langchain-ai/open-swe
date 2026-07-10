import { useNavigate } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type { AgentThread } from "@/features/agents/lib/types"
import { ReviewMainBody } from "@/features/reviews/components/ReviewMainBody"
import { api } from "@/lib/api"

// The git panel's "Review" sub-tab: the PR's Open SWE review rendered inline (no
// side panel / chat), with an expand affordance that opens the full review page.
export function ReviewTab({ thread }: { thread: AgentThread }) {
  const navigate = useNavigate()
  const pr = thread.pr
  const [owner, repo] = thread.repoFullName.split("/")
  const number = pr?.number ?? null
  const enabled = Boolean(owner && repo && number !== null)

  const detail = useQuery({
    queryKey: ["review", owner, repo, number],
    queryFn: () =>
      api.getReview(owner as string, repo as string, number as number),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, repo, number],
    queryFn: () =>
      api.getReviewDiff(owner as string, repo as string, number as number),
    enabled,
  })

  if (!enabled) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-[var(--ui-text-dim)]">
        Open a pull request to see its review here.
      </div>
    )
  }
  if (detail.isLoading) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-[var(--ui-text-dim)]">
        Loading review…
      </div>
    )
  }
  if (detail.error || !detail.data) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-[var(--ui-text-dim)]">
        No review for this pull request yet.
      </div>
    )
  }

  return (
    <ReviewMainBody
      key={detail.data.head_sha}
      detail={detail.data}
      diffFiles={diff.data?.files ?? null}
      variant="embedded"
      onExpand={() =>
        navigate({
          to: "/agents/reviews/$owner/$repo/$number",
          params: {
            owner: owner as string,
            repo: repo as string,
            number: String(number),
          },
        })
      }
    />
  )
}
