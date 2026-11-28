import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArrowLeftIcon } from "@langchain/macaw-components/icons"
import { SidebarSimpleIcon } from "@phosphor-icons/react/dist/ssr/SidebarSimple"
import { useRouter } from "@tanstack/react-router"
import { Skeleton } from "@/components/Skeleton"
import { lazy, Suspense, useLayoutEffect, useRef, type ReactNode } from "react"

import { useContextReturn } from "@/lib/contextNavigation"
import { useMediaQuery } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"
import { OpenPullRequestInput } from "./OpenPullRequestInput"
import { PILL_TAB_CLASS } from "./lib/styles"
import type { PullRequestRef } from "./page/queries"
import { INLINE_NAVIGATOR_QUERY, useReviewPage } from "./page/store"
import { ReviewTabs } from "./ReviewTabs"

const ReviewPage = lazy(async () => ({
  default: (await import("./page/ReviewPage")).ReviewPage,
}))
const Navigator = lazy(async () => ({
  default: (await import("./page/Navigator")).Navigator,
}))
const Header = lazy(async () => ({
  default: (await import("./page/Header")).Header,
}))

export function PullRequestWorkspace({
  pr,
  onClose,
  children,
}: {
  pr: PullRequestRef | null
  onClose: () => void
  children: (onSelect: () => void) => ReactNode
}) {
  const router = useRouter()
  const inline = useMediaQuery(INLINE_NAVIGATOR_QUERY)
  const prListOpen = useReviewPage((state) => state.prListOpen)
  const navigatorOpen = useReviewPage(
    (state) =>
      !state.prListOpen &&
      (inline ? state.navigatorOpen : state.narrowNavigatorOpen)
  )
  const setPrListOpen = useReviewPage((state) => state.setPrListOpen)
  const setNavigatorOpen = useReviewPage((state) => state.setNavigatorOpen)

  const hasPullRequest = pr !== null
  const listShown = !hasPullRequest || prListOpen
  const navigatorShown = hasPullRequest && navigatorOpen
  const sidebarShown = listShown || navigatorShown
  const prKey = pr && `${pr.owner}/${pr.repo}/${pr.number}`
  const titleBar =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)

  const sidebarViewport = useRef<HTMLDivElement>(null)
  const swipingSidebar = useRef(false)
  const selectPr = () => {
    if (inline || hasPullRequest) setNavigatorOpen(true)
    else setPrListOpen(false)
  }

  useLayoutEffect(() => {
    const viewport = sidebarViewport.current
    if (!prKey || !sidebarShown || !viewport) return

    const scrollToPane = () => {
      swipingSidebar.current = false
      viewport.scrollTo({ left: listShown ? 0 : viewport.clientWidth })
    }
    scrollToPane()
    // Router restoration can carry the previous PR's horizontal offset into this viewport.
    return router.subscribe("onRendered", scrollToPane)
  }, [router, prKey, sidebarShown, listShown])

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {pr && (
        <Suspense
          fallback={<div className="h-11 shrink-0 border-b border-muted" />}
        >
          <Header
            key={prKey}
            pr={pr}
            titleBar={titleBar}
            onCloseDetails={onClose}
            leading={
              <SidebarNavigation
                sidebarShown={sidebarShown}
                onToggleSidebar={() => setNavigatorOpen(!sidebarShown)}
              />
            }
          />
        </Suspense>
      )}
      <div className="flex min-h-0 min-w-0 flex-1">
        <div
          className={cn(
            "flex min-h-0 min-w-0 flex-col",
            pr
              ? "review-sidebar w-full shrink-0 overflow-hidden overscroll-x-none bg-surface-level-2 min-[1000px]:w-80 min-[1000px]:border-r min-[1000px]:border-secondary"
              : "flex-1",
            !sidebarShown && "hidden"
          )}
        >
          {pr ? (
            <div
              role="group"
              aria-label="Sidebar view"
              className="flex h-11 shrink-0 items-center gap-space-1 px-space-2"
            >
              <ReviewTabs
                variant="dropdown"
                active={listShown}
                onShowList={() => setPrListOpen(true)}
              />
              <Button
                size="sm"
                color="secondary"
                variant="plain"
                className={cn(PILL_TAB_CLASS, "review-sidebar-pr-pill")}
                aria-label={`PR #${pr.number} files and walkthrough`}
                aria-pressed={navigatorShown}
                data-selected={navigatorShown ? "" : undefined}
                onClick={() => setNavigatorOpen(true)}
              >
                {`#${pr.number}`}
              </Button>
            </div>
          ) : null}
          <div
            ref={sidebarViewport}
            onWheel={(event) => {
              if (event.deltaX !== 0 || event.shiftKey) {
                swipingSidebar.current = true
              }
            }}
            onTouchMove={() => {
              swipingSidebar.current = true
            }}
            onScrollEnd={(event) => {
              if (!swipingSidebar.current) return
              if (event.target !== event.currentTarget) return
              swipingSidebar.current = false

              if (!hasPullRequest || !event.currentTarget.clientWidth) return
              const showList =
                event.currentTarget.scrollLeft <
                event.currentTarget.clientWidth / 2
              if (showList === listShown) return
              if (showList) {
                setPrListOpen(true)
              } else {
                setNavigatorOpen(true)
              }
            }}
            className={cn(
              "flex min-h-0 min-w-0 flex-1",
              pr != null &&
                "review-sidebar-scroll no-scrollbar snap-x snap-mandatory overflow-x-auto overflow-y-hidden overscroll-x-none motion-safe:scroll-smooth"
            )}
          >
            <div
              inert={!listShown}
              aria-hidden={!listShown}
              className={cn(
                "flex min-h-0 w-full min-w-0 shrink-0 snap-start flex-col",
                pr != null && "px-space-3 pb-space-3"
              )}
            >
              {pr != null && (
                <OpenPullRequestInput
                  className="w-full pb-space-2"
                  onSelect={selectPr}
                />
              )}
              {children(selectPr)}
            </div>
            {pr && (
              <div
                inert={!navigatorShown}
                aria-hidden={!navigatorShown}
                className="min-h-0 w-full min-w-0 shrink-0 snap-start"
              >
                <Suspense fallback={<Skeleton className="m-space-3 h-56" />}>
                  <Navigator key={prKey} pr={pr} />
                </Suspense>
              </div>
            )}
          </div>
        </div>
        {pr && (
          <section
            aria-label="Selected pull request"
            className={cn(
              "min-h-0 min-w-0 flex-1 flex-col",
              inline || !sidebarShown ? "flex" : "hidden"
            )}
          >
            <Suspense fallback={<Skeleton className="m-space-3 h-56" />}>
              <ReviewPage key={prKey} pr={pr} />
            </Suspense>
          </section>
        )}
      </div>
    </div>
  )
}

function SidebarNavigation({
  sidebarShown,
  onToggleSidebar,
}: {
  sidebarShown: boolean
  onToggleSidebar: () => void
}) {
  const returnNavigation = useContextReturn("review-exit")
  return (
    <div className="flex shrink-0 items-center gap-space-1">
      <IconButton
        icon={SidebarSimpleIcon}
        label={sidebarShown ? "Hide sidebar" : "Show sidebar"}
        aria-expanded={sidebarShown}
        size="sm"
        color="secondary"
        variant="plain"
        className="size-7"
        onClick={onToggleSidebar}
      />
      <Button
        size="sm"
        color="secondary"
        variant="plain"
        leftDecorator={ArrowLeftIcon}
        aria-label={returnNavigation.label}
        className="border-0"
        onClick={returnNavigation.returnToOrigin}
      >
        <span className="max-md:sr-only">{returnNavigation.label}</span>
      </Button>
      <span aria-hidden className="ml-space-1 h-5 border-l border-muted" />
    </div>
  )
}
