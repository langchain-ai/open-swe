import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowLeftIcon, GitPullRequestIcon } from "@phosphor-icons/react"

import type { PrReviewComment } from "@/lib/api"
import { ReviewCommentsMenu } from "@/components/agents/ReviewCommentsMenu"
import { ReviewMainBody } from "@/components/agents/ReviewMainBody"
import { useSidebarControls } from "@/components/sidebar-layout"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/agents/reviews/$owner/$repo/$number")({
  component: ReviewDetailPage,
})

function ReviewDetailPage() {
  const { owner, repo, number } = Route.useParams()
  const prNumber = Number(number)
  const session = useSession()
  const sidebar = useSidebarControls()
  const sidebarCollapsed = sidebar?.collapsed ?? false
  // A comment picked from the dropdown, shown inline in the diff (not GitHub).
  const [activeComment, setActiveComment] = useState<PrReviewComment | null>(
    null
  )
  const closeActiveComment = useCallback(() => setActiveComment(null), [])

  // Collapse the global nav by default while viewing a review (roomy diff),
  // restoring the prior preference on leave. Runs once for the page's lifetime.
  const sidebarRef = useRef(sidebar)
  sidebarRef.current = sidebar
  useEffect(() => {
    const controls = sidebarRef.current
    if (!controls || controls.collapsed) return
    controls.setCollapsed(true)
    return () => controls.setCollapsed(false)
  }, [])
  const detail = useQuery({
    queryKey: ["review", owner, repo, prNumber],
    queryFn: () => api.getReview(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, repo, prNumber],
    queryFn: () => api.getReviewDiff(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
  })

  const queryClient = useQueryClient()
  const headSha = detail.data?.head_sha
  const seenShaRef = useRef(headSha)
  useEffect(() => {
    if (headSha && seenShaRef.current && headSha !== seenShaRef.current) {
      void queryClient.invalidateQueries({
        queryKey: ["reviewDiff", owner, repo, prNumber],
      })
    }
    if (headSha) seenShaRef.current = headSha
  }, [headSha, queryClient, owner, repo, prNumber])

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background text-foreground">
      <header
        className={cn(
          "flex h-12 shrink-0 items-center gap-3 border-b border-border pr-4 text-xs",
          // Clear room for the fixed collapse toggle when the sidebar is hidden.
          sidebarCollapsed ? "pl-14" : "pl-4"
        )}
      >
        <Link
          to="/agents/reviews"
          className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3.5" />
          Reviews
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="inline-flex min-w-0 items-center gap-1.5 truncate">
          <GitPullRequestIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate font-medium">
            {owner}/{repo}
            <span className="ml-1.5 font-normal text-muted-foreground">
              #{number}
            </span>
            {detail.data ? ` ${detail.data.pr.title}` : ""}
          </span>
        </span>
        {Number.isFinite(prNumber) && (
          <div className="ml-auto shrink-0">
            <ReviewCommentsMenu
              owner={owner}
              repo={repo}
              number={prNumber}
              onSelect={setActiveComment}
            />
          </div>
        )}
      </header>

      {detail.error ? (
        <div className="p-6 text-xs text-destructive">
          {detail.error.message}
        </div>
      ) : !detail.data ? (
        <div className="space-y-3 p-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : (
        <ReviewMainBody
          key={detail.data.head_sha}
          detail={detail.data}
          diffFiles={diff.data?.files ?? null}
          openComment={activeComment}
          onCloseOpenComment={closeActiveComment}
        />
      )}
    </div>
  )
}
