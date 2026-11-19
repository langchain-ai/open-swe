import { useVirtualizer } from "@tanstack/react-virtual"
import { useElementScrollRestoration } from "@tanstack/react-router"
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"

import type { OpenPullRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import { pullRequestKey } from "../lib/status"
import { PullRequestCardSkeleton } from "./PullRequestCard"

const estimatedCardHeight = 208
const estimatedRowHeight = 68
const rowGap = 12

/**
 * One scrolling list of every loaded pull request, windowed to the rows on
 * screen. Without a measurable viewport — server render, jsdom — it renders
 * every row instead, since a windowed list with no window shows nothing.
 */
export function PullRequestList({
  rows,
  available,
  compact,
  children,
  onEndReached,
  footer,
}: {
  rows: OpenPullRequest[]
  // Rows that could be shown, loaded or not. Rows arriving while the end is
  // already in view have to resume growth on their own: nothing moves on
  // screen, so no further scrolling would ask for them.
  available: number
  compact?: boolean
  children: (pr: OpenPullRequest) => ReactNode
  onEndReached: () => void
  footer?: ReactNode
}) {
  const scrollEntry = useElementScrollRestoration({ id: "pull-request-list" })
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState(0)
  useEffect(() => {
    if (!scroller || typeof ResizeObserver === "undefined") return
    const measure = () => setViewport(scroller.clientHeight)
    const observer = new ResizeObserver(measure)
    observer.observe(scroller)
    measure()
    return () => observer.disconnect()
  }, [scroller])
  // The virtualizer's functions cannot be memoized, so the React Compiler
  // skips this component; it is kept small for exactly that reason.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    initialOffset: scrollEntry?.scrollY,
    getScrollElement: () => scroller,
    estimateSize: () => (compact ? estimatedRowHeight : estimatedCardHeight),
    overscan: 4,
    getItemKey: (index) => pullRequestKey(rows[index]!),
  })
  const measurable = viewport > 0
  useLayoutEffect(() => {
    if (!measurable || !scroller) return
    virtualizer.measure()
    for (const row of scroller.querySelectorAll("[data-index]"))
      virtualizer.measureElement(row)
  }, [measurable, compact, scroller, virtualizer])
  const virtualRows = virtualizer.getVirtualItems()
  const lastRendered = virtualRows.at(-1)?.index ?? -1
  const reachedEnd = useRef(onEndReached)
  useEffect(() => {
    reachedEnd.current = onEndReached
  })
  useEffect(() => {
    if (lastRendered >= rows.length - 2 && available > rows.length)
      reachedEnd.current()
  }, [lastRendered, rows.length, available])

  return (
    <div
      ref={setScroller}
      data-scroll-restoration-id="pull-request-list"
      className="min-h-0 flex-1 overflow-y-auto"
    >
      {viewport > 0 ? (
        <ul className="relative" style={{ height: virtualizer.getTotalSize() }}>
          {virtualRows.map((row) => (
            <li
              key={row.key}
              data-index={row.index}
              ref={virtualizer.measureElement}
              className="absolute top-0 left-0 w-full"
              style={{
                transform: `translateY(${row.start}px)`,
                paddingBottom: compact ? 0 : rowGap,
              }}
            >
              {children(rows[row.index]!)}
            </li>
          ))}
        </ul>
      ) : (
        <ul className={compact ? undefined : "space-y-space-3"}>
          {rows.map((pr) => (
            <li key={pullRequestKey(pr)}>{children(pr)}</li>
          ))}
        </ul>
      )}
      {footer}
    </div>
  )
}

/** Placeholder cards while the list's first page loads. */
export function PullRequestListSkeleton({
  compact,
  label = "Loading pull requests",
}: {
  compact?: boolean
  label?: string
}) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn(
        "min-h-0 flex-1 overflow-hidden",
        !compact && "space-y-space-3"
      )}
    >
      {Array.from({ length: compact ? 8 : 4 }, (_, index) => (
        <PullRequestCardSkeleton key={index} compact={compact} />
      ))}
    </div>
  )
}
