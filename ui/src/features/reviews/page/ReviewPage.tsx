import { Button } from "@langchain/macaw-components/Button"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useContextReturn } from "@/lib/contextNavigation"
import { useEffect, useLayoutEffect, useRef, useState } from "react"

import { useMediaQuery } from "@/lib/useIsMobile"
import { RightPanelShell } from "@/features/agents/components/panel/RightPanelShell"
import { pageTitle } from "@/lib/pageTitle"
import { cn } from "@/lib/utils"
import { markReviewViewed } from "@/features/agents/lib/queries"
import { ChatDraftsProvider } from "@/features/reviews/lib/chatDrafts"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { useReviewChat } from "@/features/reviews/lib/reviewKeys"
import { Changes } from "./Changes"
import { Header } from "./Header"
import { reviewQueries, type PullRequestRef } from "./queries"
import { Rail } from "./Rail"
import { INLINE_CHAT_QUERY, useReviewPage } from "./store"
import { useReviewPageTools } from "./useReviewPageTools"

/** The selected PR, with changes and chat. */
export function ReviewPage({
  pr,
  header = true,
}: {
  pr: PullRequestRef
  header?: boolean
}) {
  const queryClient = useQueryClient()
  const detail = useQuery(reviewQueries.detail(pr))
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const errorMessage = detail.error?.message ?? refreshError
  const chatThreadId = useReviewChat(pr).data?.thread_id
  const open = useReviewPage((state) => state.open)
  const leave = useReviewPage((state) => state.leave)
  const wide = useMediaQuery(INLINE_CHAT_QUERY)
  const railOpen = useReviewPage((state) =>
    wide ? state.railOpen : state.narrowRailOpen
  )
  const setRailOpen = useReviewPage((state) => state.setRailOpen)
  const headSha = detail.data?.head_sha ?? null

  useLayoutEffect(() => open(pr, headSha), [open, pr, headSha])
  useLayoutEffect(() => leave, [leave])

  // A new head or base means a new diff, and nothing else reloads it; the
  // page stays mounted so nothing else resets.
  const baseRef = detail.data?.pr.base_ref ?? null
  const diffRevision =
    headSha && baseRef !== null ? `${baseRef}:${headSha}` : null
  const seenRevision = useRef(diffRevision)
  useEffect(() => {
    if (
      diffRevision &&
      seenRevision.current &&
      diffRevision !== seenRevision.current
    )
      void queryClient.invalidateQueries({
        queryKey: reviewQueries.diff(pr).queryKey,
      })
    if (diffRevision) seenRevision.current = diffRevision
  }, [diffRevision, queryClient, pr])

  const title = pageTitle(
    detail.data?.pr.title ?? `${pr.owner}/${pr.repo} #${pr.number}`
  )
  useEffect(() => {
    document.title = title
  }, [title])

  // Re-marked when a walkthrough lands, since its arrival is what made the row unread.
  const walkthroughSha = detail.data?.walkthrough?.head_sha
  const detailLoaded = detail.isSuccess
  useEffect(() => {
    if (chatThreadId && detailLoaded)
      markReviewViewed(queryClient, pr, chatThreadId)
  }, [queryClient, pr, chatThreadId, detailLoaded, walkthroughSha])

  useReviewPageTools(pr)

  return (
    <ChatDraftsProvider>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-surface-level-1 text-primary">
        {header && <Header pr={pr} leftInset="pl-space-3" />}
        {errorMessage !== null ? (
          <PullRequestUnavailable
            pr={pr}
            message={errorMessage}
            refreshing={detail.isFetching}
            onRefresh={() => {
              setRefreshError(errorMessage)
              void detail.refetch().finally(() => setRefreshError(null))
            }}
          />
        ) : (
          <div className="flex min-h-0 flex-1">
            <section
              aria-label="Changes"
              className={cn(
                "min-h-0 min-w-0 flex-1 flex-col",
                !wide && railOpen ? "hidden" : "flex"
              )}
            >
              <div className="min-h-0 flex-1">
                <Changes pr={pr} />
              </div>
            </section>
            <RightPanelShell
              mode={wide ? "inline" : "embedded"}
              hidden={!railOpen}
              widthStorageKey="open-swe.review-panel.width"
              defaultWidth={420}
            >
              <Rail pr={pr} onClose={() => setRailOpen(false)} />
            </RightPanelShell>
          </div>
        )}
      </div>
    </ChatDraftsProvider>
  )
}

function PullRequestUnavailable({
  pr,
  message,
  refreshing,
  onRefresh,
}: {
  pr: PullRequestRef
  message: string
  refreshing: boolean
  onRefresh: () => void
}) {
  const returnNavigation = useContextReturn("review")
  return (
    <div
      role="alert"
      className="mx-auto flex max-w-md flex-1 flex-col items-start justify-center gap-space-3 px-space-5"
    >
      <p className="text-lg font-semibold text-primary">
        Can&apos;t open {pr.owner}/{pr.repo}#{pr.number}
      </p>
      <p className="text-sm text-secondary">
        It doesn&apos;t exist, or your GitHub account can&apos;t see it. GitHub
        said: {message}.
      </p>
      <div className="flex flex-wrap gap-space-2">
        <Button
          size="sm"
          loading={refreshing}
          disabled={refreshing}
          aria-busy={refreshing}
          onClick={onRefresh}
        >
          Refresh
        </Button>
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          onClick={returnNavigation.returnToOrigin}
        >
          {returnNavigation.label}
        </Button>
        <Button
          size="sm"
          color="secondary"
          variant="plain"
          as={
            <a
              href={githubUrls.pullRequest(pr)}
              target="_blank"
              rel="noreferrer"
            />
          }
        >
          Try it on GitHub
        </Button>
      </div>
    </div>
  )
}
