import { Button } from "@langchain/macaw-components/Button"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useEffect, useLayoutEffect, useRef } from "react"

import { useIsHydrated } from "@/lib/hydration"
import { useMediaQuery } from "@/lib/useIsMobile"
import { pageTitle } from "@/lib/pageTitle"
import { SideSheet } from "@/components/SideSheet"
import { useSidebarControls } from "@/components/sidebar-layout"
import { RightPanelShell } from "@/features/agents/components/panel/RightPanelShell"
import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"
import { markReviewViewed } from "@/features/agents/lib/queries"
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
  }, [title])

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
      : "pl-space-8"
    : "pl-space-3"

  const navigatorInline = navigatorOpen && roomForNavigator

  return (
    <ChatDraftsProvider>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-surface-level-1 text-primary">
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
              <div className="w-[248px] shrink-0 border-r border-default bg-surface-level-1">
                <Navigator pr={pr} />
              </div>
            )}
            <section aria-label="Changes" className="min-w-0 flex-1">
              <Changes pr={pr} />
            </section>
            {wide && (
              <RightPanelShell
                mode="inline"
                widthStorageKey="open-swe.review-panel.width"
                defaultWidth={420}
              >
                <Rail pr={pr} />
              </RightPanelShell>
            )}
          </div>
        )}
        {!wide && (
          <SideSheet
            side="right"
            title="Chat and discussion"
            open={railOpen}
            onClose={() => setRailOpen(false)}
            className={RIGHT_PANEL_SHEET_CLASS_NAME}
          >
            <Rail pr={pr} onClose={() => setRailOpen(false)} />
          </SideSheet>
        )}
        {!roomForNavigator && (
          <SideSheet
            side="left"
            title="Files"
            open={navigatorOverlay}
            onClose={() => setNavigatorOverlay(false)}
            className="w-[min(88vw,300px)] pt-space-7"
          >
            <Navigator pr={pr} />
          </SideSheet>
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
      className="mx-auto flex max-w-md flex-1 flex-col items-start justify-center gap-space-3 px-space-5"
    >
      <p className="text-lg font-semibold text-primary">
        Can&apos;t open {pr.owner}/{pr.repo}#{pr.number}
      </p>
      <p className="text-sm text-secondary">
        It doesn&apos;t exist, or your GitHub account can&apos;t see it. GitHub
        said: {message}.
      </p>
      <div className="flex gap-space-2">
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          as={<Link to="/agents/reviews" />}
        >
          Back to reviews
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
