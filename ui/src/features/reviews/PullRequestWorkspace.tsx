import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { XIcon } from "@langchain/macaw-components/icons"
import { ListBulletsIcon } from "@phosphor-icons/react/dist/ssr/ListBullets"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { lazy, Suspense, useState, type ReactNode } from "react"

import { cn } from "@/lib/utils"
import type { PullRequestRef } from "./page/queries"

const ReviewPage = lazy(async () => ({
  default: (await import("./page/ReviewPage")).ReviewPage,
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
  const [listCollapsed, setListCollapsed] = useState(false)
  const [mobileListOpen, setMobileListOpen] = useState(false)

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <div
        className={cn(
          "min-h-0 min-w-0 flex-col",
          pr
            ? "w-full shrink-0 bg-surface-level-2 px-space-3 pb-space-3 min-[1000px]:w-80"
            : "flex flex-1",
          pr && (mobileListOpen ? "flex" : "hidden"),
          pr && (listCollapsed ? "min-[1000px]:hidden" : "min-[1000px]:flex")
        )}
      >
        {pr && mobileListOpen && (
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            className="mt-space-2 self-start border-0 min-[1000px]:hidden"
            onClick={() => setMobileListOpen(false)}
          >
            Back to selected PR
          </Button>
        )}
        {children(() => setMobileListOpen(false))}
      </div>
      {pr && (
        <section
          aria-label="Selected pull request"
          className={cn(
            "min-h-0 min-w-0 flex-1 flex-col min-[1000px]:flex",
            mobileListOpen ? "hidden" : "flex"
          )}
        >
          <div className="flex h-11 shrink-0 items-center justify-between border-b border-muted px-space-3">
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              className="hidden border-0 min-[1000px]:inline-flex"
              leftDecorator={ListBulletsIcon}
              aria-expanded={!listCollapsed}
              onClick={() => setListCollapsed(!listCollapsed)}
            >
              {listCollapsed ? "Show PR list" : "Hide PR list"}
            </Button>
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              className="border-0 min-[1000px]:hidden"
              leftDecorator={ListBulletsIcon}
              aria-expanded={mobileListOpen}
              onClick={() => setMobileListOpen(true)}
            >
              Show PR list
            </Button>
            <IconButton
              icon={XIcon}
              label="Close PR details"
              size="sm"
              color="secondary"
              variant="plain"
              onClick={onClose}
            />
          </div>
          <Suspense fallback={<Skeleton className="m-space-3 h-56" />}>
            <ReviewPage key={`${pr.owner}/${pr.repo}/${pr.number}`} pr={pr} />
          </Suspense>
        </section>
      )}
    </div>
  )
}
