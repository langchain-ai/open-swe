import { useEffect, useRef } from "react"

import { Skeleton } from "@/components/ui/skeleton"

const LINE_HEIGHT_PX = 20
const MAX_LINES = 30
/** How far ahead of the viewport a file starts loading its patch. */
const LOOKAHEAD = "1500px 0px"

function scrollParent(node: HTMLElement): HTMLElement | null {
  for (let el = node.parentElement; el; el = el.parentElement) {
    const { overflowY } = getComputedStyle(el)
    if (overflowY === "auto" || overflowY === "scroll") return el
  }
  return null
}

/**
 * Stands in for a diff whose patch has not loaded, about as tall as the diff
 * will be, and asks for it once it nears the viewport of its scroll container.
 */
export function PatchPlaceholder({
  lines,
  onNearViewport,
}: {
  lines: number
  onNearViewport: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onNearViewport()
      },
      { root: scrollParent(node), rootMargin: LOOKAHEAD }
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [onNearViewport])
  const shown = Math.max(1, Math.min(lines, MAX_LINES))
  return (
    <div
      ref={ref}
      aria-busy="true"
      aria-label="Loading diff"
      className="space-y-1.5 bg-card p-3"
      style={{ height: shown * LINE_HEIGHT_PX + 24 }}
    >
      {Array.from({ length: Math.min(shown, 6) }, (_, index) => (
        <Skeleton
          key={index}
          className="h-3"
          style={{ width: `${40 + ((index * 37) % 55)}%` }}
        />
      ))}
    </div>
  )
}
