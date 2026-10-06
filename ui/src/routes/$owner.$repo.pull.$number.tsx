import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect, useMemo, useRef } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"

import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { IconWell } from "@langchain/gtm-platform-design-system/ui/icon-well"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import {
  AlertTriangle,
  ExternalLink,
  GitHub,
  GitPullRequest,
} from "@/components/glyphs"
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
      <Stack
        render={<main />}
        align="center"
        justify="center"
        bg="canvas"
        padding="xl"
        className="min-h-svh"
      >
        <Skeleton className="h-52 w-full max-w-lg rounded-panel" />
      </Stack>
    )
  }

  if (!session.data) return <RequireLogin />

  if (!Number.isFinite(prNumber)) {
    return (
      <ReviewLinkCard
        title="Invalid pull request link"
        description="Expected a GitHub-style pull request path like /owner/repo/pull/123."
        failure="ATTENTION"
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
        failure="RISK"
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
  failure,
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
  /** Who clears it: a failed start is a wall, a bad link the reader fixes. */
  failure?: "RISK" | "ATTENTION"
  onRetry?: () => void
}) {
  return (
    <Stack
      render={<main />}
      align="center"
      justify="center"
      bg="canvas"
      padding="xl"
      className="min-h-svh text-ink"
    >
      <Stack
        gap="lg"
        bg="panel"
        border="line"
        radius="panel"
        padding="xl"
        className="w-full max-w-lg"
      >
        {failure ? (
          <StateNotice
            tone={failure}
            icon={AlertTriangle}
            title={title}
            description={description}
          />
        ) : (
          <Inline gap="md" align="start">
            <IconWell>
              {loading ? (
                <Spinner size="sm" />
              ) : (
                <Icon icon={GitPullRequest} size="sm" />
              )}
            </IconWell>
            <Stack gap="xs" className="min-w-0">
              <Box
                render={<h1 />}
                className="text-title font-semibold text-ink"
              >
                {title}
              </Box>
              <p className="text-body text-ink-subtle">{description}</p>
            </Stack>
          </Inline>
        )}
        <Inline
          gap="md"
          justify="between"
          bg="muted"
          border="line"
          radius="compact"
          padding="md"
          wrap
        >
          <Inline gap="sm" className="min-w-0 text-label">
            <Icon icon={GitHub} size="sm" className="text-ink-subtle" />
            <span className="truncate font-medium">
              {owner}/{repo}
            </span>
            <span className="font-mono text-ink-subtle">#{number}</span>
          </Inline>
          <a
            href={githubPrUrl}
            className="inline-flex items-center gap-1 text-label text-ink-subtle hover:text-ink"
          >
            View on GitHub
            <Icon icon={ExternalLink} size="sm" />
          </a>
        </Inline>
        <Inline gap="sm" justify="end" wrap>
          <a
            href={githubPrUrl}
            className={buttonVariants({ variant: "ghost" })}
          >
            Open GitHub PR
          </a>
          {stableReviewPath && (
            <Link
              to="/agents/reviews/$owner/$repo/$number"
              params={{ owner, repo, number }}
              className={buttonVariants({ variant: "outline" })}
            >
              Open stable review page
            </Link>
          )}
          {onRetry && <Button onClick={onRetry}>Try again</Button>}
        </Inline>
      </Stack>
    </Stack>
  )
}
