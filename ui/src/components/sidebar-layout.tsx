import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"
import { createPortal } from "react-dom"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArrowRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowRight"
import { SidebarSimpleIcon } from "@phosphor-icons/react/dist/ssr/SidebarSimple"

import { shortcutPlatform, useShortcutLabel } from "@/lib/hotkeys"
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
  // On mobile the sidebar is a full-screen overlay, so start collapsed to keep
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

  return {
    width,
    collapsed,
    setWidth,
    setCollapsed,
    /** For a page that wants the room while it is open; the stored preference is untouched. */
    setCollapsedForPage: setCollapsedState,
    toggle,
    closeOnMobile,
  }
}

export type SidebarLayout = ReturnType<typeof useSidebarLayout>

// Shares the single sidebar-layout instance with page content so it can react
// to the collapsed state (e.g. clear room for the fixed collapse toggle).
const SidebarLayoutContext = createContext<SidebarLayout | null>(null)

export function SidebarLayoutProvider({
  value,
  children,
}: {
  value: SidebarLayout
  children: React.ReactNode
}) {
  return (
    <SidebarLayoutContext.Provider value={value}>
      {children}
    </SidebarLayoutContext.Provider>
  )
}

export function useSidebarCollapsed(): boolean {
  return useContext(SidebarLayoutContext)?.collapsed ?? false
}

// Full sidebar controls (collapse/expand) for pages that want to drive the nav,
// e.g. the review detail page collapsing it by default. Null outside the provider.
export function useSidebarControls(): SidebarLayout | null {
  return useContext(SidebarLayoutContext)
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
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  // Rendered by the root layout in the same commit, so it exists once mounted.
  const [controlsHost, setControlsHost] = useState<Element | null>(null)
  useEffect(() => {
    if (!isDesktop) return
    // oxlint-disable-next-line react/set-state-in-effect
    setControlsHost(document.querySelector("[data-desktop-window-controls]"))
  }, [isDesktop])
  const windowControls =
    controlsHost &&
    createPortal(
      <WindowControls collapsed={collapsed} toggle={toggle} />,
      controlsHost
    )

  if (collapsed) {
    if (isDesktop) return windowControls
    return createPortal(
      <IconButton
        icon={SidebarSimpleIcon}
        label="Expand sidebar"
        color="secondary"
        variant="plain"
        size="sm"
        onClick={toggle}
        className="fixed top-2 left-2 z-sidebar size-7"
      />,
      document.body
    )
  }

  return (
    <aside
      data-sidebar-frame=""
      style={{ width }}
      className={cn(
        "relative flex h-svh shrink-0 flex-col",
        "max-md:fixed max-md:inset-0 max-md:z-pane max-md:!w-full",
        "max-md:duration-normal max-md:ease-out max-md:animate-in max-md:fade-in-0 max-md:slide-in-from-left-4",
        className
      )}
    >
      {children}
      <div className="max-md:hidden">
        <ResizeHandle width={width} onResize={setWidth} />
      </div>
      {windowControls}
    </aside>
  )
}

/**
 * The desktop title bar's buttons beside the traffic lights: the sidebar
 * toggle, then back and forward, which the app needs without browser chrome.
 * One no-drag cluster in the root layout's controls host, so no draggable
 * header can come after it and swallow its clicks. ⌘[ and ⌘] stay the
 * browser's on the web.
 */
function WindowControls({
  collapsed,
  toggle,
}: {
  collapsed: boolean
  toggle: () => void
}) {
  const subscribe = (onChange: () => void) => {
    window.navigation?.addEventListener("currententrychange", onChange)
    return () =>
      window.navigation?.removeEventListener("currententrychange", onChange)
  }
  const canGoBack = useSyncExternalStore(
    subscribe,
    () => window.navigation?.canGoBack ?? true,
    () => false
  )
  const canGoForward = useSyncExternalStore(
    subscribe,
    () => window.navigation?.canGoForward ?? true,
    () => false
  )
  const backShortcut = useShortcutLabel("mod+[")
  const forwardShortcut = useShortcutLabel("mod+]")

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const mod = shortcutPlatform() === "mac" ? event.metaKey : event.ctrlKey
      if (!mod || event.altKey || event.shiftKey) return
      if (event.key === "[") window.history.back()
      else if (event.key === "]") window.history.forward()
      else return
      event.preventDefault()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  return (
    <div
      data-no-drag=""
      className="fixed top-2 left-[90px] z-sidebar flex items-center gap-0.5 [.desktop-fullscreen_&]:left-[12px]"
    >
      <IconButton
        icon={SidebarSimpleIcon}
        label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        color="secondary"
        variant="plain"
        size="sm"
        onClick={toggle}
        className="size-7"
      />
      <IconButton
        icon={ArrowLeftIcon}
        label="Back"
        tooltipProps={{
          title: (
            <span className="flex items-center gap-space-2">
              Back <Kbd>{backShortcut}</Kbd>
            </span>
          ),
        }}
        color="secondary"
        variant="plain"
        size="sm"
        disabled={!canGoBack}
        onClick={() => window.history.back()}
        className="size-7 disabled:bg-transparent"
      />
      <IconButton
        icon={ArrowRightIcon}
        label="Forward"
        tooltipProps={{
          title: (
            <span className="flex items-center gap-space-2">
              Forward <Kbd>{forwardShortcut}</Kbd>
            </span>
          ),
        }}
        color="secondary"
        variant="plain"
        size="sm"
        disabled={!canGoForward}
        onClick={() => window.history.forward()}
        className="size-7 disabled:bg-transparent"
      />
    </div>
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
        "absolute top-0 right-0 z-resize-handle h-full w-1 cursor-col-resize touch-none select-none",
        "after:absolute after:inset-y-0 after:right-0 after:border-r after:border-transparent after:transition-colors",
        "hover:after:border-default",
        dragging && "after:border-default"
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
  // On desktop the title bar's WindowControls own the toggle.
  if (typeof window !== "undefined" && window.openSweDesktop) return null
  return (
    <IconButton
      icon={SidebarSimpleIcon}
      label="Collapse sidebar"
      color="secondary"
      variant="plain"
      size="sm"
      onClick={onToggle}
      className={cn("shrink-0", className)}
    />
  )
}
