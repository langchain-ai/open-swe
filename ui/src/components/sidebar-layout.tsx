import { useCallback, useEffect, useRef, useState } from "react"
import { SidebarSimpleIcon } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

const STORAGE_WIDTH = "open-swe.sidebar.width"
const STORAGE_COLLAPSED = "open-swe.sidebar.collapsed"

export const SIDEBAR_DEFAULT_WIDTH = 260
export const SIDEBAR_MIN_WIDTH = 200
export const SIDEBAR_MAX_WIDTH = 420

function readStoredWidth(): number {
  if (typeof window === "undefined") return SIDEBAR_DEFAULT_WIDTH
  const raw = window.localStorage.getItem(STORAGE_WIDTH)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return SIDEBAR_DEFAULT_WIDTH
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, parsed))
}

function readStoredCollapsed(): boolean {
  if (typeof window === "undefined") return false
  // On mobile the sidebar opens as an overlay, so start collapsed to keep
  // the chat visible regardless of the stored desktop preference.
  if (window.matchMedia("(max-width: 767px)").matches) return true
  return window.localStorage.getItem(STORAGE_COLLAPSED) === "1"
}

export function useSidebarLayout() {
  const [width, setWidthState] = useState<number>(() => readStoredWidth())
  const [collapsed, setCollapsedState] = useState<boolean>(() =>
    readStoredCollapsed()
  )

  const setWidth = useCallback((next: number) => {
    const clamped = Math.min(
      SIDEBAR_MAX_WIDTH,
      Math.max(SIDEBAR_MIN_WIDTH, next)
    )
    setWidthState(clamped)
    window.localStorage.setItem(STORAGE_WIDTH, String(clamped))
  }, [])

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next)
    window.localStorage.setItem(STORAGE_COLLAPSED, next ? "1" : "0")
  }, [])

  const toggle = useCallback(
    () => setCollapsed(!collapsed),
    [collapsed, setCollapsed]
  )

  const closeOnMobile = useCallback(() => {
    if (typeof window === "undefined") return
    // State-only: don't persist, so the desktop collapsed preference is preserved.
    if (window.matchMedia("(max-width: 767px)").matches) setCollapsedState(true)
  }, [])

  return { width, collapsed, setWidth, setCollapsed, toggle, closeOnMobile }
}

interface SidebarFrameProps {
  width: number
  setWidth: (next: number) => void
  collapsed: boolean
  toggle: () => void
  className?: string
  children: React.ReactNode
}

export function SidebarFrame({
  width,
  setWidth,
  collapsed,
  toggle,
  className,
  children,
}: SidebarFrameProps) {
  if (collapsed) {
    return (
      <button
        type="button"
        aria-label="Expand sidebar"
        onClick={toggle}
        className="fixed top-4 left-4 z-30 flex size-12 touch-manipulation items-center justify-center rounded-full border border-border bg-background/95 text-muted-foreground shadow-lg transition-colors hover:bg-accent hover:text-foreground md:top-3 md:left-3 md:size-7 md:rounded-md md:shadow-sm"
      >
        <SidebarSimpleIcon className="size-5 md:size-4" />
      </button>
    )
  }

  return (
    <>
      <button
        type="button"
        aria-label="Close sidebar"
        onClick={toggle}
        className="fixed inset-0 z-30 hidden bg-black/35 max-md:block"
      />
      <aside
        style={{ width }}
        className={cn(
          "relative z-40 flex h-svh shrink-0 flex-col",
          "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:!w-[min(88vw,420px)] max-md:max-w-[420px] max-md:shadow-2xl",
          className
        )}
      >
        {children}
        <div className="max-md:hidden">
          <ResizeHandle width={width} onResize={setWidth} />
        </div>
      </aside>
    </>
  )
}

function ResizeHandle({
  width,
  onResize,
}: {
  width: number
  onResize: (next: number) => void
}) {
  const startRef = useRef<{ x: number; width: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    startRef.current = { x: e.clientX, width }
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!startRef.current) return
    const next = startRef.current.width + (e.clientX - startRef.current.x)
    onResize(next)
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    startRef.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  useEffect(() => {
    if (!dragging) return
    const prev = document.body.style.cursor
    document.body.style.cursor = "col-resize"
    return () => {
      document.body.style.cursor = prev
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
        "absolute top-0 right-0 z-20 h-full w-1 cursor-col-resize touch-none select-none",
        "after:absolute after:inset-y-0 after:right-0 after:w-px after:bg-transparent after:transition-colors",
        "hover:after:bg-border",
        dragging && "after:bg-border"
      )}
    />
  )
}

interface SidebarCollapseButtonProps {
  onToggle: () => void
  className?: string
}

export function SidebarCollapseButton({
  onToggle,
  className,
}: SidebarCollapseButtonProps) {
  return (
    <button
      type="button"
      aria-label="Collapse sidebar"
      onClick={onToggle}
      className={cn(
        "flex size-11 shrink-0 touch-manipulation items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground md:size-6 md:rounded",
        className
      )}
    >
      <SidebarSimpleIcon className="size-5 md:size-4" />
    </button>
  )
}
