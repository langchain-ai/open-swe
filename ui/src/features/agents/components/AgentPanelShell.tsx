import { useCallback, useEffect, useRef, useState } from "react"
import {
  ArrowsInIcon,
  ArrowsOutIcon,
  SidebarSimpleIcon,
} from "@phosphor-icons/react"
import {
  FileDiff,
  Folder,
  Globe,
  ListChecks,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react"

import type { PanelTab, PanelTabKind } from "@/features/agents/lib/panelTabs"
import { isMultiInstanceKind } from "@/features/agents/lib/panelTabs"
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { Z } from "@/features/agents/components/z-index"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

const PANEL_TAB_META: Record<
  PanelTabKind,
  { label: string; hint?: string; Icon: typeof FileDiff }
> = {
  review: { label: "Review", hint: "⌃⇧G", Icon: FileDiff },
  terminal: { label: "Terminal", Icon: SquareTerminal },
  browser: { label: "Browser", Icon: Globe },
  files: { label: "Files", Icon: Folder },
  plan: { label: "Plan", Icon: ListChecks },
}

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

function PanelControl({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        aria-label={label}
        className="rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground"
        onClick={onClick}
        type="button"
      >
        {children}
      </TooltipTrigger>
      <TooltipPopup>{label}</TooltipPopup>
    </Tooltip>
  )
}

function PanelLauncher({
  kinds,
  onOpen,
}: {
  kinds: ReadonlyArray<PanelTabKind>
  onOpen: (kind: PanelTabKind) => void
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col justify-center gap-1 p-3">
      {kinds.map((kind) => {
        const { label, hint, Icon } = PANEL_TAB_META[kind]
        return (
          <button
            key={kind}
            type="button"
            onClick={() => onOpen(kind)}
            className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm text-foreground transition-colors hover:bg-accent"
          >
            <Icon className="size-4 text-muted-foreground" />
            <span>{label}</span>
            {hint && (
              <span className="ml-auto text-xs tracking-widest text-muted-foreground/60">
                {hint}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function PanelComingSoon() {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-6 text-xs text-muted-foreground/70">
      Coming Soon
    </div>
  )
}

interface AgentPanelShellProps {
  tabs: ReadonlyArray<PanelTab>
  activeTabId: string | null
  onSelectTab: (id: string) => void
  /** Omit to make tabs non-closable (cloud threads). */
  onCloseTab?: (id: string) => void
  onOpenKind?: (kind: PanelTabKind) => void
  /** Kinds offered by the launcher and the "+" menu. */
  menuKinds: ReadonlyArray<PanelTabKind>
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
  seamlessHeader?: boolean
  /** Rendered as the panel body; `fullScreen` drives layout-only extras. */
  children: (state: { fullScreen: boolean }) => React.ReactNode
}

/**
 * The resizable right-hand column shared by cloud threads and local desktop
 * sessions: a tab strip with a "+" menu, panel controls, and the body the
 * active tab renders into. On mobile it becomes a full-screen overlay.
 */
export function AgentPanelShell({
  tabs,
  activeTabId,
  onSelectTab,
  onCloseTab,
  onOpenKind,
  menuKinds,
  collapsed,
  onCollapsedChange,
  seamlessHeader = false,
  children,
}: AgentPanelShellProps) {
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
        aria-label="Show panel"
        title="Show panel"
        className="fixed top-2 right-2 z-30 flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <SidebarSimpleIcon className="size-4" />
      </button>
    )
  }

  const openableKinds = onOpenKind
    ? menuKinds.filter(
        (kind) =>
          isMultiInstanceKind(kind) || !tabs.some((tab) => tab.kind === kind)
      )
    : []

  return (
    <aside
      ref={panelRef}
      className={cn(
        "relative flex shrink-0 flex-col bg-background",
        overlay ? "fixed inset-0 !w-full" : "h-full border-l border-border"
      )}
      style={overlay ? { zIndex: Z.MODAL } : { width }}
    >
      <div
        className={cn(
          "flex h-11 shrink-0 items-center px-2",
          !seamlessHeader && "border-b border-border"
        )}
      >
        <div className="flex min-w-0 flex-1 items-center overflow-x-auto">
          {tabs.map((tab, index) => {
            const { label, Icon } = PANEL_TAB_META[tab.kind]
            const title = tab.title ?? label
            const active = tab.id === activeTabId
            return (
              <div key={tab.id} className="flex min-w-0 items-center">
                {index > 0 && (
                  <div
                    className={cn(
                      "h-4 w-px shrink-0 bg-border",
                      (active || tabs[index - 1]?.id === activeTabId) &&
                        "bg-transparent"
                    )}
                  />
                )}
                <div
                  className={cn(
                    "flex min-w-0 items-center rounded-md",
                    active && "bg-accent"
                  )}
                >
                  <button
                    type="button"
                    aria-current={active ? "page" : undefined}
                    onClick={() => onSelectTab(tab.id)}
                    title={title}
                    className={cn(
                      "flex min-w-0 items-center gap-1.5 px-2 py-1 text-xs transition-colors",
                      active
                        ? "font-medium text-foreground"
                        : "text-muted-foreground/70 hover:text-foreground"
                    )}
                  >
                    <Icon className="size-3.5 shrink-0" />
                    <span className="max-w-28 truncate">{title}</span>
                  </button>
                  {active && onCloseTab && tab.closable !== false && (
                    <button
                      type="button"
                      aria-label={`Close ${title}`}
                      onClick={() => onCloseTab(tab.id)}
                      className="pr-1.5 text-muted-foreground/70 transition-colors hover:text-foreground"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              </div>
            )
          })}
          {openableKinds.length > 0 && (
            <Menu>
              <MenuTrigger
                aria-label="Open a new tab"
                className="ml-1 shrink-0 rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground"
              >
                <Plus className="size-4" />
              </MenuTrigger>
              <MenuPopup align="start" className="w-44">
                {openableKinds.map((kind) => {
                  const { label, hint, Icon } = PANEL_TAB_META[kind]
                  return (
                    <MenuItem key={kind} onClick={() => onOpenKind?.(kind)}>
                      <Icon />
                      {label}
                      {hint && (
                        <span className="ml-auto tracking-widest text-muted-foreground/60">
                          {hint}
                        </span>
                      )}
                    </MenuItem>
                  )
                })}
              </MenuPopup>
            </Menu>
          )}
        </div>
        <div className="flex shrink-0 items-center">
          {!isMobile && (
            <PanelControl
              label={fullScreen ? "Exit full screen" : "Expand panel"}
              onClick={() => setFullScreen((v) => !v)}
            >
              {fullScreen ? (
                <ArrowsInIcon className="size-4" />
              ) : (
                <ArrowsOutIcon className="size-4" />
              )}
            </PanelControl>
          )}
          <PanelControl
            label="Hide panel"
            onClick={() => {
              setFullScreen(false)
              onCollapsedChange(true)
            }}
          >
            <SidebarSimpleIcon className="size-4" />
          </PanelControl>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {tabs.length === 0 && onOpenKind ? (
          <PanelLauncher kinds={menuKinds} onOpen={onOpenKind} />
        ) : (
          children({ fullScreen })
        )}
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
