import { Link, createFileRoute } from "@tanstack/react-router"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"

import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { AlertTriangle, ArrowLeft, GitPullRequest } from "@/components/glyphs"
import { useRailCollapsed } from "@/components/rail/useRailCollapsed"
import type { PrReviewComment } from "@/lib/api"
import { navLink } from "@/features/reviews/PullRequestLinks"
import { ReviewCommentsMenu } from "@/features/reviews/components/ReviewCommentsMenu"
import { ReviewMainBody } from "@/features/reviews/components/ReviewMainBody"
import { SubmitReviewPopover } from "@/features/reviews/components/SubmitReviewPopover"
import {
  markReviewViewed,
  reviewChatQuery,
} from "@/features/agents/lib/queries"
import { reviewOpenedFromSidebar } from "@/features/reviews/lib/reviewEntry"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { api } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents/reviews/$owner/$repo/$number")({
  component: ReviewDetailPage,
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
  const prNumber = Number(number)
  const session = useSession()
  const rail = useRailCollapsed()
  // A comment picked from the dropdown, shown inline in the diff (not GitHub).
  const [activeComment, setActiveComment] = useState<PrReviewComment | null>(
    null
  )
  const closeActiveComment = useCallback(() => setActiveComment(null), [])
  const updateActiveComment = useCallback(
    (comment: PrReviewComment) =>
      setActiveComment((current) =>
        current?.id === comment.id ? comment : current
      ),
    []
  )

  // Collapse the global nav by default while viewing a review (roomy diff),
  // restoring the prior preference on leave. Runs once for the page's lifetime.
  const railRef = useRef(rail)
  const openedFromSidebar = useRef(
    reviewOpenedFromSidebar({ owner, repo, number: prNumber })
  )
  useEffect(() => {
    railRef.current = rail
  }, [rail])
  useEffect(() => {
    const controls = railRef.current
    if (controls.collapsed || openedFromSidebar.current) return
    controls.setCollapsed(true)
    return () => controls.setCollapsed(false)
  }, [])
  const detail = useQuery({
    queryKey: ["review", owner, repo, prNumber],
    queryFn: () => api.getReview(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
    refetchInterval: (query) =>
      query.state.data?.status === "running" ||
      query.state.data?.walkthrough_running
        ? 5000
        : false,
  })
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, repo, prNumber],
    queryFn: () => api.getReviewDiff(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
  })

  const queryClient = useQueryClient()
  const headSha = detail.data?.head_sha
  const seenShaRef = useRef(headSha)
  const prTitle = detail.data?.pr.title
  const documentTitle = pageTitle(prTitle ?? `${owner}/${repo} #${prNumber}`)
  useEffect(() => {
    document.title = documentTitle
  }, [documentTitle])
  useEffect(() => {
    if (headSha && seenShaRef.current && headSha !== seenShaRef.current) {
      void queryClient.invalidateQueries({
        queryKey: ["reviewDiff", owner, repo, prNumber],
      })
    }
    if (headSha) seenShaRef.current = headSha
  }, [headSha, queryClient, owner, repo, prNumber])

  const reviewChatThreadId = useQuery({
    ...reviewChatQuery({ owner, repo, number: prNumber }),
    enabled: !!session.data && Number.isFinite(prNumber),
  }).data?.thread_id
  // Re-marked when a walkthrough lands, since its arrival is what made the row unread.
  const walkthroughSha = detail.data?.walkthrough?.head_sha
  useEffect(() => {
    if (!reviewChatThreadId) return
    markReviewViewed(
      queryClient,
      { owner, repo, number: prNumber },
      reviewChatThreadId
    )
  }, [queryClient, owner, repo, prNumber, reviewChatThreadId, walkthroughSha])

  if (session.isLoading) {
    return (
      <Box render={<main />} padding="xl" className="flex-1">
        <Skeleton className="h-64 w-full rounded-panel" />
      </Box>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <Stack className="min-w-0 flex-1 overflow-hidden bg-canvas text-ink">
      <Inline
        render={<header />}
        data-desktop-drag-region=""
        gap="sm"
        className="h-toolbar shrink-0 border-b border-line px-4 text-label"
      >
        <Link to="/agents/reviews" className={navLink}>
          <Icon icon={ArrowLeft} size="sm" />
          Reviews
        </Link>
        <span aria-hidden="true" className="text-ink-subtle">
          /
        </span>
        <Inline gap="sm" className="min-w-0 flex-1">
          <Icon icon={GitPullRequest} size="sm" className="text-ink-subtle" />
          <span className="min-w-0 truncate font-medium">
            {owner}/{repo}
            <span className="ml-1.5 font-mono font-normal text-ink-subtle">
              #{number}
            </span>
            {detail.data ? ` ${detail.data.pr.title}` : ""}
          </span>
        </Inline>
        {Number.isFinite(prNumber) && (
          <Inline gap="sm" className="shrink-0">
            <ReviewCommentsMenu
              owner={owner}
              repo={repo}
              number={prNumber}
              onSelect={setActiveComment}
            />
            <SubmitReviewPopover owner={owner} repo={repo} number={prNumber} />
          </Inline>
        )}
      </Inline>

      {detail.error ? (
        <Box padding="xl" className="mx-auto w-full max-w-reading">
          <StateNotice
            tone="RISK"
            icon={AlertTriangle}
            title="Could not load this review"
            description={detail.error.message}
            action={
              <Button
                size="compact"
                variant="outline"
                onClick={() => void detail.refetch()}
              >
                Try again
              </Button>
            }
          />
        </Box>
      ) : !detail.data ? (
        <Stack gap="md" padding="xl">
          <Skeleton className="h-24 w-full rounded-panel" />
          <Skeleton className="h-96 w-full rounded-panel" />
        </Stack>
      ) : (
        <ReviewMainBody
          key={detail.data.head_sha}
          detail={detail.data}
          diffFiles={diff.data?.files ?? null}
          openComment={activeComment}
          onUpdateOpenComment={updateActiveComment}
          onCloseOpenComment={closeActiveComment}
        />
      )}
    </Stack>
  )
}
