import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useLayoutEffect, useRef } from "react"
import { Link } from "@tanstack/react-router"

import { useIsHydrated } from "@/lib/hydration"
import { Button } from "@/components/ui/button"
import { useMediaQuery } from "@/lib/useIsMobile"
import { pageTitle } from "@/lib/pageTitle"
import { useResizableWidth } from "@/lib/useResizableWidth"
import { Sheet, SheetPopup, SheetTitle } from "@/components/ui/sheet"
import { useSidebarControls } from "@/components/sidebar-layout"
import { RightPanelResizeHandle } from "@/features/agents/components/panel/RightPanelResizeHandle"
import {
  agentThreadKeys,
  markReviewViewed,
} from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { ChatDraftsProvider } from "@/features/reviews/lib/chatDrafts"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { reviewOpenedFromSidebar } from "@/features/reviews/lib/reviewEntry"
import { useReviewChat } from "@/features/reviews/lib/reviewKeys"
import { Changes } from "./Changes"
import { Header } from "./Header"
import { Navigator } from "./Navigator"
import { reviewQueries, type PullRequestRef } from "./queries"
import { Rail } from "./Rail"
import { NAVIGATOR_INLINE_QUERY, useReviewPage } from "./store"

/** The pull request page: files on the left, the PR and its code in the middle, the agent on the right. */
export function ReviewPage({ pr }: { pr: PullRequestRef }) {
  const queryClient = useQueryClient()
  const detail = useQuery(reviewQueries.detail(pr))
  const chatThreadId = useReviewChat(pr).data?.thread_id
  // The chat's thread view retitles the tab with its own name; watch for that and take it back.
  const chatTitle = useQuery({
    queryKey: agentThreadKeys.detail(chatThreadId ?? ""),
    queryFn: skipToken,
    select: (thread: AgentThread) => thread.title,
  }).data
  const open = useReviewPage((state) => state.open)
  const navigatorOpen = useReviewPage((state) => state.navigatorOpen)
  const navigatorOverlay = useReviewPage((state) => state.navigatorOverlay)
  const setNavigatorOverlay = useReviewPage(
    (state) => state.setNavigatorOverlay
  )
  const railOpen = useReviewPage((state) => state.railOpen)
  const setRailOpen = useReviewPage((state) => state.setRailOpen)
  // The server and the first client render assume a desktop window, so hydration matches.
  const hydrated = useIsHydrated()
  const wideQuery = useMediaQuery("(min-width: 1100px)")
  const navigatorQuery = useMediaQuery(NAVIGATOR_INLINE_QUERY)
  const wide = !hydrated || wideQuery
  const roomForNavigator = !hydrated || navigatorQuery
  const headSha = detail.data?.head_sha ?? null

  useLayoutEffect(() => open(pr, headSha), [open, pr, headSha])

  // A new head means a new diff; the page stays mounted so nothing else resets.
  const seenSha = useRef(headSha)
  useEffect(() => {
    if (headSha && seenSha.current && headSha !== seenSha.current)
      void queryClient.invalidateQueries({
        queryKey: reviewQueries.diff(pr).queryKey,
      })
    if (headSha) seenSha.current = headSha
  }, [headSha, queryClient, pr])

  const title = pageTitle(
    detail.data?.pr.title ?? `${pr.owner}/${pr.repo} #${pr.number}`
  )
  useEffect(() => {
    document.title = title
  }, [title, chatTitle])

  // Re-marked when a walkthrough lands, since its arrival is what made the row unread.
  const walkthroughSha = detail.data?.walkthrough?.head_sha
  const detailLoaded = detail.isSuccess
  useEffect(() => {
    if (chatThreadId && detailLoaded)
      markReviewViewed(queryClient, pr, chatThreadId)
  }, [queryClient, pr, chatThreadId, detailLoaded, walkthroughSha])

  useCollapseAppSidebar(pr)
  const sidebar = useSidebarControls()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const leftInset = sidebar?.collapsed
    ? isDesktop
      ? "pl-32"
      : "pl-14"
    : "pl-3"

  const rail = useResizableWidth({
    storageKey: "open-swe.review-panel.width",
    defaultWidth: 420,
    minWidth: 340,
    maxWidth: 680,
    edge: "left",
  })

  const navigatorInline = navigatorOpen && roomForNavigator

  return (
    <ChatDraftsProvider>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background text-foreground">
        <Header
          pr={pr}
          leftInset={leftInset}
          compactRail={!wide}
          navigatorShown={roomForNavigator ? navigatorOpen : navigatorOverlay}
        />
        {detail.isError ? (
          <PullRequestUnavailable pr={pr} message={detail.error.message} />
        ) : (
          <div className="flex min-h-0 flex-1">
            {navigatorInline && (
              <div className="w-[248px] shrink-0 border-r border-border bg-background">
                <Navigator pr={pr} />
              </div>
            )}
            <section aria-label="Changes" className="min-w-0 flex-1">
              <Changes pr={pr} />
            </section>
            {wide && (
              <div
                className="relative shrink-0 border-l border-border"
                style={{ width: rail.width }}
              >
                <RightPanelResizeHandle handlers={rail.handlers} />
                <Rail pr={pr} />
              </div>
            )}
          </div>
        )}
        {!wide && (
          <Sheet open={railOpen} onOpenChange={setRailOpen}>
            <SheetPopup
              side="right"
              keepMounted
              showCloseButton={false}
              className="max-w-[440px] p-0"
            >
              <Rail pr={pr} onClose={() => setRailOpen(false)} />
            </SheetPopup>
          </Sheet>
        )}
        {!roomForNavigator && (
          <Sheet open={navigatorOverlay} onOpenChange={setNavigatorOverlay}>
            <SheetPopup side="left" className="max-w-[300px] p-0 pt-10">
              <SheetTitle className="sr-only">Files</SheetTitle>
              <Navigator pr={pr} />
            </SheetPopup>
          </Sheet>
        )}
      </div>
    </ChatDraftsProvider>
  )
}

function PullRequestUnavailable({
  pr,
  message,
}: {
  pr: PullRequestRef
  message: string
}) {
  return (
    <div
      role="alert"
      className="mx-auto flex max-w-md flex-1 flex-col items-start justify-center gap-3 px-6"
    >
      <p className="text-[17px] font-semibold text-foreground">
        Can&apos;t open {pr.owner}/{pr.repo}#{pr.number}
      </p>
      <p className="text-sm text-muted-foreground">
        It doesn&apos;t exist, or your GitHub account can&apos;t see it. GitHub
        said: {message}.
      </p>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          render={<Link to="/agents/reviews" />}
        >
          Back to reviews
        </Button>
        <Button
          variant="ghost"
          size="sm"
          render={
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

/** Collapse the app's nav for room, unless the person came from it; restore it on the way out. */
function useCollapseAppSidebar(pr: PullRequestRef) {
  const sidebar = useSidebarControls()
  const sidebarRef = useRef(sidebar)
  const fromSidebar = useRef(reviewOpenedFromSidebar(pr))
  useEffect(() => {
    sidebarRef.current = sidebar
  }, [sidebar])
  useLayoutEffect(() => {
    const controls = sidebarRef.current
    if (!controls || controls.collapsed || fromSidebar.current) return
    controls.setCollapsedForPage(true)
    return () => controls.setCollapsedForPage(false)
  }, [])
}
