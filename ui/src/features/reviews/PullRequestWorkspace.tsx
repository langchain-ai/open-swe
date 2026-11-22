import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArrowLeftIcon, XIcon } from "@langchain/macaw-components/icons"
import { SidebarSimpleIcon } from "@phosphor-icons/react/dist/ssr/SidebarSimple"
import { Skeleton } from "@/components/Skeleton"
import { lazy, Suspense, type ReactNode } from "react"

import { useContextReturn } from "@/lib/contextNavigation"
import { useMediaQuery } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"
import { OpenPullRequestInput } from "./OpenPullRequestInput"
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
const NavigatorSwitch = lazy(async () => ({
  default: (await import("./page/ReadingOrderTabs")).NavigatorSwitch,
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
  const inline = useMediaQuery(INLINE_NAVIGATOR_QUERY)
  const prListOpen = useReviewPage((state) => state.prListOpen)
  const navigatorOpen = useReviewPage(
    (state) =>
      !state.prListOpen &&
      (inline ? state.navigatorOpen : state.narrowNavigatorOpen)
  )
  const setPrListOpen = useReviewPage((state) => state.setPrListOpen)
  const setNavigatorOpen = useReviewPage((state) => state.setNavigatorOpen)
  const selectPr = () => setPrListOpen(false)
  const listShown = !pr || prListOpen
  const navigatorShown = Boolean(pr) && navigatorOpen
  const sidebarShown = listShown || navigatorShown
  const prKey = pr && `${pr.owner}/${pr.repo}/${pr.number}`
  // On desktop an open PR's header is the title bar, and one switch in the sidebar picks the list, walkthrough or files.
  const titleBar =
    Boolean(pr) &&
    typeof window !== "undefined" &&
    Boolean(window.openSweDesktop)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {pr && titleBar && (
        <Suspense
          fallback={<div className="h-11 shrink-0 border-b border-muted" />}
        >
          <Header
            key={prKey}
            pr={pr}
            titleBar
            leftInset="pl-[80px] [.desktop-fullscreen_&]:pl-space-3 [.version-mismatch_&]:pl-space-3"
            leading={
              <TitleBarNavigation
                sidebarShown={sidebarShown}
                onToggleSidebar={() => setNavigatorOpen(!sidebarShown)}
              />
            }
            trailing={
              <IconButton
                icon={XIcon}
                label="Close PR details"
                size="sm"
                color="secondary"
                variant="plain"
                onClick={onClose}
              />
            }
          />
        </Suspense>
      )}
      {pr && !titleBar && (
        <div className="flex min-h-11 shrink-0 flex-wrap items-center gap-space-1 border-b border-muted px-space-3">
          <Button
            size="sm"
            color="secondary"
            variant={listShown ? "normal" : "plain"}
            aria-pressed={listShown}
            onClick={() => setPrListOpen(!listShown)}
          >
            PR list
          </Button>
          <Button
            size="sm"
            color="secondary"
            variant={navigatorShown ? "normal" : "plain"}
            aria-pressed={navigatorShown}
            title="Show or hide Files & Walkthrough (F)"
            onClick={() => setNavigatorOpen(!navigatorShown)}
          >
            Files &amp; Walkthrough
          </Button>
          {!inline && (
            <Button
              size="sm"
              color="secondary"
              variant={sidebarShown ? "plain" : "normal"}
              aria-pressed={!sidebarShown}
              onClick={() => {
                setNavigatorOpen(false)
                useReviewPage.getState().setRailOpen(false)
              }}
            >
              Review
            </Button>
          )}
          <IconButton
            className="ml-auto"
            icon={XIcon}
            label="Close PR details"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={onClose}
          />
        </div>
      )}
      <div className="flex min-h-0 min-w-0 flex-1">
        <div
          className={cn(
            "grid min-h-0 min-w-0 grid-cols-1",
            titleBar ? "grid-rows-[auto_minmax(0,1fr)]" : "grid-rows-1",
            pr
              ? "w-full shrink-0 bg-surface-level-2 min-[1000px]:w-80"
              : "flex-1",
            !sidebarShown && "hidden"
          )}
        >
          {pr && titleBar && (
            <Suspense fallback={<div className="h-11" />}>
              <NavigatorSwitch
                pr={pr}
                listShown={listShown}
                onShowList={() => setPrListOpen(true)}
              />
            </Suspense>
          )}
          <div
            inert={!listShown}
            aria-hidden={!listShown}
            className={cn(
              "col-start-1 flex min-h-0 min-w-0 flex-col motion-safe:transition-[opacity,visibility] motion-safe:duration-normal",
              titleBar ? "row-start-2" : "row-start-1",
              pr && "px-space-3 pb-space-3",
              !listShown && "invisible opacity-0"
            )}
          >
            {titleBar && (
              <div className="flex flex-col gap-space-2 pb-space-2">
                <ReviewTabs keepSelection />
                <OpenPullRequestInput className="w-full" />
              </div>
            )}
            {children(selectPr)}
          </div>
          {pr && (
            <div
              inert={!navigatorShown}
              aria-hidden={!navigatorShown}
              className={cn(
                "col-start-1 min-h-0 min-w-0 motion-safe:transition-[opacity,visibility] motion-safe:duration-normal",
                titleBar ? "row-start-2" : "row-start-1",
                !navigatorShown && "invisible opacity-0"
              )}
            >
              <Suspense fallback={<Skeleton className="m-space-3 h-56" />}>
                <Navigator key={prKey} pr={pr} orderTabs={!titleBar} />
              </Suspense>
            </div>
          )}
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
              <ReviewPage key={prKey} pr={pr} header={!titleBar} />
            </Suspense>
          </section>
        )}
      </div>
    </div>
  )
}

function TitleBarNavigation({
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
