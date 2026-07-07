import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect, useMemo, useRef } from "react"
import { ArrowSquareOutIcon, GitPullRequestIcon } from "@phosphor-icons/react"
import { useMutation } from "@tanstack/react-query"

import { buttonVariants } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

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
  const triggerRef = useRef<string | null>(null)
  const triggerReview = useMutation({
    mutationFn: () => api.reReview(owner, repo, prNumber),
  })

  useEffect(() => {
    if (!session.data || !Number.isFinite(prNumber)) return
    const key = `${owner}/${repo}#${prNumber}`
    if (triggerRef.current === key) return
    triggerRef.current = key
    triggerReview.mutate()
  }, [owner, repo, prNumber, session.data, triggerReview])

  if (session.isLoading) {
    return (
      <main className="flex min-h-svh items-center justify-center p-6">
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

  if (triggerReview.isSuccess) {
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

  return (
    <ReviewLinkCard
      title="Starting Open SWE review"
      description="This PR link was recognized. Open SWE is starting a review and will redirect you to the stable review page."
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
    <main className="flex min-h-svh items-center justify-center bg-background p-6 text-foreground">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <GitPullRequestIcon className="size-5 text-muted-foreground" />
            {title}
          </CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
            <div className="font-medium">
              {owner}/{repo} #{number}
            </div>
            <a
              href={githubPrUrl}
              className="mt-1 inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              View on GitHub
              <ArrowSquareOutIcon className="size-3.5" />
            </a>
          </div>
          {loading && <Skeleton className="mt-4 h-2 w-full" />}
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2">
          {onRetry && (
            <button
              type="button"
              className={buttonVariants()}
              onClick={onRetry}
            >
              Try again
            </button>
          )}
          {stableReviewPath && (
            <Link
              to="/agents/reviews/$owner/$repo/$number"
              params={{ owner, repo, number }}
              className={cn(buttonVariants({ variant: "outline" }))}
            >
              Open stable review page
            </Link>
          )}
          <a
            href={githubPrUrl}
            className={cn(buttonVariants({ variant: "ghost" }))}
          >
            Open GitHub PR
          </a>
        </CardFooter>
      </Card>
    </main>
  )
}
