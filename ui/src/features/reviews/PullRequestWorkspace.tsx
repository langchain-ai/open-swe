import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { XIcon } from "@langchain/macaw-components/icons"
import { Skeleton } from "@/components/Skeleton"
import { lazy, Suspense, type ReactNode } from "react"

import { useMediaQuery } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"
import type { PullRequestRef } from "./page/queries"
import { INLINE_NAVIGATOR_QUERY, useReviewPage } from "./page/store"

const ReviewPage = lazy(async () => ({
  default: (await import("./page/ReviewPage")).ReviewPage,
}))
const Navigator = lazy(async () => ({
  default: (await import("./page/Navigator")).Navigator,
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

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {pr && (
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
            "grid min-h-0 min-w-0 grid-cols-1 grid-rows-1",
            pr
              ? "w-full shrink-0 bg-surface-level-2 min-[1000px]:w-80"
              : "flex-1",
            !sidebarShown && "hidden"
          )}
        >
          <div
            inert={!listShown}
            aria-hidden={!listShown}
            className={cn(
              "col-start-1 row-start-1 flex min-h-0 min-w-0 flex-col motion-safe:transition-[opacity,visibility] motion-safe:duration-normal",
              pr && "px-space-3 pb-space-3",
              !listShown && "invisible opacity-0"
            )}
          >
            {children(selectPr)}
          </div>
          {pr && (
            <div
              inert={!navigatorShown}
              aria-hidden={!navigatorShown}
              className={cn(
                "col-start-1 row-start-1 min-h-0 min-w-0 motion-safe:transition-[opacity,visibility] motion-safe:duration-normal",
                !navigatorShown && "invisible opacity-0"
              )}
            >
              <Suspense fallback={<Skeleton className="m-space-3 h-56" />}>
                <Navigator key={prKey} pr={pr} />
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
              <ReviewPage key={prKey} pr={pr} />
            </Suspense>
          </section>
        )}
      </div>
    </div>
  )
}
