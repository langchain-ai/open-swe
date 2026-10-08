import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"

import { useIsHydrated } from "@/lib/hydration"
import { useMediaQuery } from "@/lib/useIsMobile"
import { pageTitle } from "@/lib/pageTitle"
import { Sheet, SheetPopup } from "@/components/ui/sheet"
import { useSidebarControls } from "@/components/sidebar-layout"
import {
  agentThreadKeys,
  markReviewViewed,
  reviewChatQuery,
} from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { ChatDraftsProvider } from "@/features/reviews/lib/chatDrafts"
import { reviewOpenedFromSidebar } from "@/features/reviews/lib/reviewEntry"
import { Changes } from "./Changes"
import { Header } from "./Header"
import { Navigator } from "./Navigator"
import { reviewQueries, type PullRequestRef } from "./queries"
import { Rail } from "./Rail"
import { NAVIGATOR_INLINE_QUERY, useReviewPage } from "./store"

const RAIL_WIDTH_KEY = "open-swe.review-panel.width"
const RAIL_MIN = 340
const RAIL_MAX = 680
const RAIL_DEFAULT = 420

function readRailWidth(): number {
  if (typeof window === "undefined") return RAIL_DEFAULT
  try {
    const stored = Number(window.localStorage.getItem(RAIL_WIDTH_KEY))
    return Number.isFinite(stored) && stored >= RAIL_MIN
      ? Math.min(stored, RAIL_MAX)
      : RAIL_DEFAULT
  } catch (error) {
    console.warn("Could not read the chat width", error)
    return RAIL_DEFAULT
  }
}

/** The pull request page: files on the left, the PR and its code in the middle, the agent on the right. */
export function ReviewPage({ pr }: { pr: PullRequestRef }) {
  const queryClient = useQueryClient()
  const detail = useQuery(reviewQueries.detail(pr))
  const chatThreadId = useQuery(reviewChatQuery(pr)).data?.thread_id
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
  useEffect(() => {
    if (chatThreadId) markReviewViewed(queryClient, pr, chatThreadId)
  }, [queryClient, pr, chatThreadId, walkthroughSha])

  useCollapseAppSidebar(pr)
  const sidebar = useSidebarControls()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const leftInset = sidebar?.collapsed
    ? isDesktop
      ? "pl-32"
      : "pl-14"
    : "pl-3"

  const [draggedWidth, setRailWidth] = useState<number | null>(null)
  const railWidth = draggedWidth ?? (hydrated ? readRailWidth() : RAIL_DEFAULT)
  const startResize = useResize(railWidth, setRailWidth)

  const navigatorInline = navigatorOpen && roomForNavigator

  return (
    <ChatDraftsProvider>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background text-foreground">
        <Header pr={pr} leftInset={leftInset} compactRail={!wide} />
        {detail.isError ? (
          <div className="p-6 text-xs text-destructive">
            {detail.error.message}
          </div>
        ) : (
          <div className="flex min-h-0 flex-1">
            {navigatorInline && (
              <div className="w-[248px] shrink-0 border-r border-border bg-[color-mix(in_oklab,var(--background)_97%,var(--foreground))]">
                <Navigator pr={pr} />
              </div>
            )}
            <main className="min-w-0 flex-1">
              <Changes pr={pr} />
            </main>
            {wide && (
              <>
                <div
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize the chat"
                  onPointerDown={startResize}
                  className="group relative w-px shrink-0 cursor-col-resize bg-border"
                >
                  <span className="absolute inset-y-0 -left-1.5 w-3 group-hover:bg-primary/15" />
                </div>
                <div className="shrink-0" style={{ width: railWidth }}>
                  <Rail pr={pr} />
                </div>
              </>
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
            <SheetPopup
              side="left"
              showCloseButton={false}
              className="max-w-[300px] p-0"
            >
              <Navigator pr={pr} />
            </SheetPopup>
          </Sheet>
        )}
      </div>
    </ChatDraftsProvider>
  )
}

function useResize(width: number, setWidth: (width: number) => void) {
  return useCallback(
    (event: React.PointerEvent) => {
      event.preventDefault()
      const startX = event.clientX
      const startWidth = width
      let latest = width
      const move = (moveEvent: PointerEvent) => {
        latest = Math.min(
          RAIL_MAX,
          Math.max(RAIL_MIN, startWidth + startX - moveEvent.clientX)
        )
        setWidth(latest)
      }
      const up = () => {
        window.removeEventListener("pointermove", move)
        window.removeEventListener("pointerup", up)
        document.body.style.removeProperty("cursor")
        try {
          window.localStorage.setItem(RAIL_WIDTH_KEY, String(latest))
        } catch (error) {
          console.warn("Could not save the chat width", error)
        }
      }
      document.body.style.cursor = "col-resize"
      window.addEventListener("pointermove", move)
      window.addEventListener("pointerup", up)
    },
    [width, setWidth]
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
  useEffect(() => {
    const controls = sidebarRef.current
    if (!controls || controls.collapsed || fromSidebar.current) return
    controls.setCollapsed(true)
    return () => controls.setCollapsed(false)
  }, [])
}
