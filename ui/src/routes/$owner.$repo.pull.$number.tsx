import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Text } from "@langchain/macaw-components/Text"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect, useMemo, useRef } from "react"

import { api } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/$owner/$repo/pull/$number")({
  component: PullRequestReviewLinkPage,
})

function PullRequestReviewLinkPage() {
  const { owner, repo, number } = Route.useParams()
  const prNumber = Number(number)
  const session = useSession()
  const stableReviewPath = `/agents/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${prNumber}`
  const githubPrUrl = useMemo(
    () => `https://github.com/${owner}/${repo}/pull/${number}`,
    [owner, repo, number]
  )
  const existingReview = useQuery({
    queryKey: ["review", owner, repo, prNumber],
    queryFn: () => api.getReview(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
    retry: false,
  })
  const triggerRef = useRef<string | null>(null)
  const triggerReview = useMutation({
    mutationFn: () => api.reReview(owner, repo, prNumber),
    meta: { silent: true },
  })
  const { mutate: triggerReviewMutate } = triggerReview

  useEffect(() => {
    if (!session.data || !Number.isFinite(prNumber)) return
    if (existingReview.isLoading || existingReview.data?.status === "running") {
      return
    }
    const key = `${owner}/${repo}#${prNumber}`
    if (triggerRef.current === key) return
    triggerRef.current = key
    triggerReviewMutate()
  }, [
    existingReview.data?.status,
    existingReview.isLoading,
    owner,
    repo,
    prNumber,
    session.data,
    triggerReviewMutate,
  ])

  if (session.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center p-space-5">
        <Skeleton className="h-52 w-full max-w-lg" />
      </main>
    )
  }

  if (!session.data) return <RequireLogin />

  if (!Number.isFinite(prNumber)) {
    return (
      <ReviewLinkCard
        title="Invalid pull request link"
        description="Expected a GitHub-style pull request path like /owner/repo/pull/123."
        owner={owner}
        repo={repo}
        number={number}
        githubPrUrl={githubPrUrl}
      />
    )
  }

  if (existingReview.data?.status === "running" || triggerReview.isSuccess) {
    return (
      <Navigate
        to="/agents/reviews/$owner/$repo/$number"
        params={{ owner, repo, number: String(prNumber) }}
        replace
      />
    )
  }

  if (triggerReview.isError) {
    return (
      <ReviewLinkCard
        title="Could not start review"
        description={triggerReview.error.message}
        owner={owner}
        repo={repo}
        number={number}
        githubPrUrl={githubPrUrl}
        stableReviewPath={stableReviewPath}
        onRetry={() => triggerReview.mutate()}
      />
    )
  }

  const isCheckingExistingReview = existingReview.isLoading

  return (
    <ReviewLinkCard
      title={
        isCheckingExistingReview
          ? "Checking review status"
          : "Starting Open SWE review"
      }
      description={
        isCheckingExistingReview
          ? "This PR link was recognized. Open SWE is checking whether a review is already running."
          : "Open SWE is starting a review and will redirect you to the stable review page."
      }
      owner={owner}
      repo={repo}
      number={number}
      githubPrUrl={githubPrUrl}
      stableReviewPath={stableReviewPath}
      loading
    />
  )
}

function ReviewLinkCard({
  title,
  description,
  owner,
  repo,
  number,
  githubPrUrl,
  stableReviewPath,
  loading = false,
  onRetry,
}: {
  title: string
  description: string
  owner: string
  repo: string
  number: string
  githubPrUrl: string
  stableReviewPath?: string
  loading?: boolean
  onRetry?: () => void
}) {
  return (
    <main className="flex min-h-svh items-center justify-center bg-surface-level-1 p-space-5 text-primary">
      <Card className="flex w-full max-w-lg flex-col gap-space-4 p-space-5">
        <div className="flex flex-col gap-space-1">
          <Text
            as="h1"
            variant="h3"
            weight="semibold"
            className="flex items-center gap-space-2"
          >
            <GitPullRequestIcon
              weight="regular"
              className="size-5 text-icon-secondary"
            />
            {title}
          </Text>
          <Text variant="sm" color="secondary">
            {description}
          </Text>
        </div>
        <div>
          <div className="rounded-lg border border-default bg-surface-level-1 p-space-3 text-sm">
            <div className="font-medium">
              {owner}/{repo} #{number}
            </div>
            <MacawLink
              href={githubPrUrl}
              variant="sm"
              rightDecorator={ArrowSquareOutIcon}
              className="mt-space-1"
            >
              View on GitHub
            </MacawLink>
          </div>
          {loading && <Skeleton className="mt-space-4 h-2 w-full" />}
        </div>
        <div className="flex flex-wrap gap-space-2">
          {onRetry && <Button onClick={onRetry}>Try again</Button>}
          {stableReviewPath && (
            <Button
              color="secondary"
              variant="outlined"
              as={
                <Link
                  to="/agents/reviews/$owner/$repo/$number"
                  params={{ owner, repo, number }}
                />
              }
            >
              Open stable review page
            </Button>
          )}
          <Button
            color="secondary"
            variant="plain"
            as={<a href={githubPrUrl} />}
          >
            Open GitHub PR
          </Button>
        </div>
      </Card>
    </main>
  )
}
