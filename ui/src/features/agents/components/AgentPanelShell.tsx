import { useCallback, useEffect, useRef, useState } from "react"
import {
  ArrowsInIcon,
  ArrowsOutIcon,
  SidebarSimpleIcon,
} from "@phosphor-icons/react"

import { Z } from "@/features/agents/components/z-index"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const PANEL_STORAGE_WIDTH = "open-swe.gitpanel.width"
const PANEL_DEFAULT_WIDTH = 420
const PANEL_MIN_WIDTH = 320
// Keep at least this much room for the chat so the panel can grow to nearly the
// full window (e.g. ~50/50 on ultrawide screens) without squishing the chat.
// Exported so the chat column can enforce the same floor via min-width.
export const PANEL_MIN_CHAT_WIDTH = 360

function getPanelMaxWidth(availableWidth?: number): number {
  if (typeof window === "undefined") return PANEL_DEFAULT_WIDTH
  const available = availableWidth ?? window.innerWidth
  return Math.max(PANEL_MIN_WIDTH, available - PANEL_MIN_CHAT_WIDTH)
}

function clampPanelWidth(width: number, availableWidth?: number): number {
  return Math.min(
    getPanelMaxWidth(availableWidth),
    Math.max(PANEL_MIN_WIDTH, width)
  )
}

function readStoredPanelWidth(): number {
  if (typeof window === "undefined") return PANEL_DEFAULT_WIDTH
  const raw = window.localStorage.getItem(PANEL_STORAGE_WIDTH)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return PANEL_DEFAULT_WIDTH
  return clampPanelWidth(parsed)
}

function PanelResizeHandle({
  width,
  onResize,
  onResizeEnd,
}: {
  width: number
  onResize: (next: number) => number
  onResizeEnd: (next: number) => void
}) {
  const startRef = useRef<{ x: number; width: number } | null>(null)
  const pendingWidthRef = useRef<number | null>(null)
  const latestWidthRef = useRef(width)
  const frameRef = useRef<number | null>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    latestWidthRef.current = width
  }, [width])

  const flushResize = useCallback(() => {
    frameRef.current = null
    const next = pendingWidthRef.current
    pendingWidthRef.current = null
    if (next == null) return
    latestWidthRef.current = onResize(next)
  }, [onResize])

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    startRef.current = { x: e.clientX, width: latestWidthRef.current }
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!startRef.current) return
    pendingWidthRef.current =
      startRef.current.width - (e.clientX - startRef.current.x)
    if (frameRef.current == null) {
      frameRef.current = window.requestAnimationFrame(flushResize)
    }
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (frameRef.current != null) {
      window.cancelAnimationFrame(frameRef.current)
      flushResize()
    }
    startRef.current = null
    setDragging(false)
    onResizeEnd(latestWidthRef.current)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  useEffect(() => {
    return () => {
      if (frameRef.current != null) {
        window.cancelAnimationFrame(frameRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!dragging) return
    const prevCursor = document.body.style.cursor
    const prevUserSelect = document.body.style.userSelect
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    return () => {
      document.body.style.cursor = prevCursor
      document.body.style.userSelect = prevUserSelect
    }
  }, [dragging])

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      className={cn(
        // Straddles the seam so the grab strip never sits on top of panel content.
        "absolute top-0 -left-1 z-20 h-full w-2 cursor-col-resize touch-none select-none",
        "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-transparent after:transition-colors",
        "hover:after:bg-border",
        dragging && "after:bg-border"
      )}
    />
  )
}

interface AgentPanelShellProps<TTab extends string> {
  tabs: ReadonlyArray<readonly [TTab, string]>
  activeTab: TTab
  onTabChange: (tab: TTab) => void
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
  /** Rendered inside the panel card; `fullScreen` drives layout-only extras. */
  children: (state: { fullScreen: boolean }) => React.ReactNode
}

/**
 * The resizable right-hand column shared by cloud threads and local desktop
 * sessions: tab bar, collapse/full-screen controls, and the card the active tab
 * renders into. On mobile it becomes a full-screen overlay instead of a column.
 */
export function AgentPanelShell<TTab extends string>({
  tabs,
  activeTab,
  onTabChange,
  collapsed,
  onCollapsedChange,
  children,
}: AgentPanelShellProps<TTab>) {
  const [width, setWidthState] = useState(() => readStoredPanelWidth())
  const [fullScreen, setFullScreen] = useState(false)
  const isMobile = useIsMobile()
  // On mobile the panel is never an inline resizable column — it's a full-screen
  // overlay that the user navigates to (and back from), like the sidebar.
  const overlay = fullScreen || isMobile
  const panelRef = useRef<HTMLElement>(null)

  const applyWidth = useCallback(
    (next: number) => {
      const available = panelRef.current?.parentElement?.clientWidth
      const clamped = clampPanelWidth(next, available)
      if (!overlay && panelRef.current) {
        panelRef.current.style.width = `${clamped}px`
      }
      return clamped
    },
    [overlay]
  )

  const commitWidth = useCallback(
    (next: number) => {
      const clamped = applyWidth(next)
      setWidthState((current) => (current === clamped ? current : clamped))
      if (typeof window !== "undefined") {
        window.localStorage.setItem(PANEL_STORAGE_WIDTH, String(clamped))
      }
    },
    [applyWidth]
  )

  // Re-clamp against the real container width on mount and whenever the window
  // resizes, so the panel can never squeeze the chat below its minimum width.
  useEffect(() => {
    if (typeof window === "undefined") return
    const reclamp = () => commitWidth(width)
    reclamp()
    window.addEventListener("resize", reclamp)
    return () => window.removeEventListener("resize", reclamp)
  }, [commitWidth, width])

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => onCollapsedChange(false)}
        aria-label="Expand panel"
        title="Expand panel"
        className="fixed top-3 right-3 z-30 flex size-7 items-center justify-center rounded-md border border-border bg-background text-muted-foreground shadow-sm hover:bg-accent hover:text-foreground"
      >
        <SidebarSimpleIcon className="size-4" />
      </button>
    )
  }

  return (
    <aside
      ref={panelRef}
      className={cn(
        // No background or rule of its own: the panel shares the main surface
        // (and its grain) with the conversation. The seam is the gap between
        // the two cards; the resize handle only paints on hover.
        "relative flex shrink-0 flex-col",
        overlay ? "fixed inset-0 !w-full bg-background" : "h-full"
      )}
      style={overlay ? { zIndex: Z.MODAL } : { width }}
    >
      <div className="flex h-11 shrink-0 items-center gap-1 px-3">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            type="button"
            aria-current={activeTab === id ? "page" : undefined}
            onClick={() => onTabChange(id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              activeTab === id
                ? "bg-accent font-medium text-foreground"
                : "text-muted-foreground/70 hover:bg-accent"
            )}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            setFullScreen(false)
            onCollapsedChange(true)
          }}
          aria-label="Collapse panel"
          title="Collapse panel"
          className="ml-auto rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground"
        >
          <SidebarSimpleIcon className="size-4" />
        </button>
        {!isMobile && (
          <button
            type="button"
            onClick={() => setFullScreen((v) => !v)}
            aria-label={fullScreen ? "Exit full screen" : "Enter full screen"}
            className="rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground"
          >
            {fullScreen ? (
              <ArrowsInIcon className="size-4" />
            ) : (
              <ArrowsOutIcon className="size-4" />
            )}
          </button>
        )}
      </div>

      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card",
          // No left margin: the seam is the chat column's own `px-4`, so the gap
          // to the composer matches the gap on the sidebar side.
          overlay ? "mx-3 mb-3" : "mr-4 mb-4"
        )}
      >
        {children({ fullScreen })}
      </div>
      {!overlay && (
        <PanelResizeHandle
          width={width}
          onResize={applyWidth}
          onResizeEnd={commitWidth}
        />
      )}
    </aside>
  )
}
