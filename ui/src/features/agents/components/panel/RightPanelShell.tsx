import { useEffect, useLayoutEffect, useRef, useState } from "react"
import type { ReactNode, RefObject } from "react"

import { useResizableWidth } from "@/lib/useResizableWidth"
import { cn } from "@/lib/utils"
import { RightPanelResizeHandle } from "@/features/agents/components/panel/RightPanelResizeHandle"

export type RightPanelMode = "inline" | "sheet" | "embedded"

const RIGHT_PANEL_WIDTH_STORAGE_KEY = "open-swe:right-panel-width"
const RIGHT_PANEL_MIN_WIDTH = 360
/**
 * Upper bound as a fraction of the viewport; only binds on wide screens.
 * On narrow windows the container clamp below is what preserves the
 * sibling column's space.
 */
const RIGHT_PANEL_MAX_WIDTH_FRACTION = 0.7
const RIGHT_PANEL_DEFAULT_WIDTH = 540
/**
 * Width reserved for the chat column sharing the panel's flex row. The
 * viewport fraction alone is not enough: the app sidebar sits outside the row,
 * so on narrow windows the remaining 30% of the viewport minus the sidebar
 * leaves the chat below its usable width and the composer overflows.
 */
export const SIBLING_COLUMN_MIN_WIDTH = 360

export function getRightPanelMaxWidth(
  viewportWidth: number,
  containerWidth?: number
): number {
  const fractionCap = Math.floor(viewportWidth * RIGHT_PANEL_MAX_WIDTH_FRACTION)
  const containerCap =
    containerWidth === undefined
      ? Infinity
      : Math.floor(containerWidth) - SIBLING_COLUMN_MIN_WIDTH
  // Never below the panel's own minimum: when the row cannot fit both columns'
  // minimums the chat yields, and useResizableWidth's clamp must not see
  // max < min (it would resolve the inversion to min and, via drag-end
  // persistence, overwrite the user's stored width).
  return Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(fractionCap, containerCap))
}

/**
 * Shell for the right panel. In inline mode the panel is user-resizable via a
 * drag handle on the left edge; width persists per browser. In sheet and
 * embedded modes the parent owns the size.
 */
export function RightPanelShell(props: {
  mode: RightPanelMode
  maximized?: boolean
  /**
   * Overrides the localStorage key used to persist the panel width. Callers
   * embedding this shell for a different surface should pass their own key so
   * resizing one panel doesn't clobber the other's remembered width.
   */
  widthStorageKey?: string
  /** Overrides the initial width (px) before the user has resized the panel. */
  defaultWidth?: number
  children: ReactNode
}) {
  const isInline = props.mode === "inline"
  const hostRef = useRef<HTMLDivElement | null>(null)
  // Only inline non-maximized mode applies `width`/`maxWidth`; skip the
  // container measurement (and its re-renders) everywhere else.
  const maxWidth = useClampedMaxWidth(hostRef, isInline && !props.maximized)
  const { width, handlers } = useResizableWidth({
    storageKey: props.widthStorageKey ?? RIGHT_PANEL_WIDTH_STORAGE_KEY,
    defaultWidth: props.defaultWidth ?? RIGHT_PANEL_DEFAULT_WIDTH,
    minWidth: RIGHT_PANEL_MIN_WIDTH,
    maxWidth,
    edge: "left",
  })

  return (
    <div
      ref={hostRef}
      className={cn(
        "relative flex h-full min-h-0 max-w-full min-w-0 flex-col self-stretch bg-background",
        isInline
          ? props.maximized
            ? "flex-1 border-l border-border"
            : "shrink-0 border-l border-border"
          : "w-full"
      )}
      style={isInline && !props.maximized ? { width: `${width}px` } : undefined}
      data-right-panel-mode={props.mode}
      data-right-panel-maximized={props.maximized ? "true" : "false"}
    >
      {isInline && !props.maximized ? (
        <RightPanelResizeHandle handlers={handlers} />
      ) : null}
      {props.children}
    </div>
  )
}

/**
 * Track viewport and flex-row widths to derive an upper bound for the panel.
 * Resize-aware so dragging the OS window narrower (or expanding the app
 * sidebar) re-clamps the stored width on the next render. The row is observed
 * rather than the panel itself because the panel competes with the chat column
 * for row space.
 */
function useClampedMaxWidth(
  hostRef: RefObject<HTMLDivElement | null>,
  enabled: boolean
): number {
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === "undefined" ? 1280 : window.innerWidth
  )
  const [containerWidth, setContainerWidth] = useState<number | undefined>(
    undefined
  )
  useEffect(() => {
    if (typeof window === "undefined") return
    let frame = 0
    const onResize = () => {
      // Coalesce rapid resize events into one rAF tick.
      if (frame !== 0) return
      frame = window.requestAnimationFrame(() => {
        frame = 0
        setViewportWidth(window.innerWidth)
      })
    }
    window.addEventListener("resize", onResize)
    return () => {
      window.removeEventListener("resize", onResize)
      if (frame !== 0) window.cancelAnimationFrame(frame)
    }
  }, [])
  useLayoutEffect(() => {
    if (!enabled) return
    const parent = hostRef.current?.parentElement
    if (!parent) return
    // Measure before first paint: the persisted width must be clamped against
    // the row on the initial render, not one observer tick later (the panel
    // would flash over-wide on every mount).
    const measure = () => setContainerWidth(parent.clientWidth)
    measure()
    if (typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(measure)
    observer.observe(parent)
    return () => observer.disconnect()
  }, [hostRef, enabled])
  return getRightPanelMaxWidth(viewportWidth, containerWidth)
}
