import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useMemo } from "react"

import { api } from "@/lib/api"

import { ReviewPage } from "@/features/reviews/page/ReviewPage"
import { warmReviewPage } from "@/features/reviews/page/queries"
import { RequireLogin } from "@/lib/auth-redirect"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents/reviews/$owner/$repo/$number")({
  component: ReviewDetailPage,
  validateSearch: (
    search: Record<string, unknown>
  ): { reviewLink?: boolean } => ({
    reviewLink: search.reviewLink === true || search.reviewLink === "true",
  }),
  // Runs on hover intent too: the shell's data and the highlighter warm up
  // before the click lands. Never awaited, so navigation is never held.
  loader: ({ context, params }) =>
    warmReviewPage(context.queryClient, {
      owner: params.owner,
      repo: params.repo,
      number: Number(params.number),
    }),
  head: ({
    params,
  }: {
    params: { owner: string; repo: string; number: string }
  }) => ({
    meta: [
      { title: pageTitle(`${params.owner}/${params.repo} #${params.number}`) },
    ],
  }),
})

function ReviewDetailPage() {
  const { owner, repo, number } = Route.useParams()
  const session = useSession()
  const { reviewLink } = Route.useSearch()
  const profile = useQuery({
    queryKey: ["profile"],
    queryFn: () => api.profile(),
    enabled: !!session.data && reviewLink,
  })
  const openGitHub = reviewLink && profile.data?.pr_review_links === false
  useEffect(() => {
    if (openGitHub && /^[1-9]\d*$/.test(number)) {
      window.location.replace(
        `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pull/${number}`
      )
    }
  }, [openGitHub, owner, repo, number])
  const pr = useMemo(
    () => ({ owner, repo, number: Number(number) }),
    [owner, repo, number]
  )
  if (
    session.isLoading ||
    (session.data && reviewLink && profile.isPending) ||
    openGitHub
  )
    return (
      <main className="p-space-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  if (!session.data) return <RequireLogin />
  if (!Number.isFinite(pr.number))
    return (
      <main className="p-space-6 text-xs text-error-secondary">
        {number} is not a pull request number.
      </main>
    )
  return <ReviewPage key={`${owner}/${repo}/${number}`} pr={pr} />
}
