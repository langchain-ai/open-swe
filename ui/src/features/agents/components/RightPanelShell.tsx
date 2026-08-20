import { useCallback, useEffect, useRef, useState } from "react"
import {
  ArrowsInIcon,
  ArrowsOutIcon,
  SidebarSimpleIcon,
} from "@phosphor-icons/react"
import {
  File,
  FileDiff,
  Files,
  GitPullRequest,
  Globe2,
  Plus,
  SquareTerminal,
  X,
} from "lucide-react"

import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import type { RightPanelSurface } from "@/features/agents/lib/rightPanelStore"
import { Z } from "@/features/agents/components/z-index"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

export type RightPanelLauncherKind =
  "preview" | "terminal" | "files" | "diff" | "pull-request"

export interface RightPanelLauncherItem {
  kind: RightPanelLauncherKind
  available: boolean
  unavailableHint: string
  disabledReason: string
}

const META = {
  preview: {
    label: "Browser",
    description: "Open a local app or URL.",
    shortcut: "B",
    Icon: Globe2,
  },
  terminal: {
    label: "Terminal",
    description: "Start a shell in this workspace.",
    shortcut: "T",
    Icon: SquareTerminal,
  },
  files: {
    label: "Files",
    description: "Browse and edit workspace files.",
    shortcut: "F",
    Icon: Files,
  },
  diff: {
    label: "Diff",
    description: "Review worktree changes.",
    shortcut: "D",
    Icon: FileDiff,
  },
  "pull-request": {
    label: "Pull request",
    description: "Review the current pull request.",
    shortcut: "P",
    Icon: GitPullRequest,
  },
} as const

const PANEL_STORAGE_WIDTH = "open-swe.right-panel.width"
const PANEL_DEFAULT_WIDTH = 480
const PANEL_MIN_WIDTH = 360
export const PANEL_MIN_CHAT_WIDTH = 360

function clampWidth(width: number, available = window.innerWidth) {
  return Math.min(
    Math.max(PANEL_MIN_WIDTH, available - PANEL_MIN_CHAT_WIDTH),
    Math.max(PANEL_MIN_WIDTH, width)
  )
}

function readWidth() {
  if (typeof window === "undefined") return PANEL_DEFAULT_WIDTH
  const parsed = Number(window.localStorage.getItem(PANEL_STORAGE_WIDTH))
  return Number.isFinite(parsed) ? clampWidth(parsed) : PANEL_DEFAULT_WIDTH
}

function titleOf(
  surface: RightPanelSurface,
  terminalLabels: ReadonlyMap<string, string>
) {
  if (surface.kind === "diff") return "Diff"
  if (surface.kind === "files") return "Files"
  if (surface.kind === "pull-request") return "Pull request"
  if (surface.kind === "preview") return "Browser"
  if (surface.kind === "terminal")
    return terminalLabels.get(surface.resourceId) ?? "Terminal"
  return surface.relativePath.slice(surface.relativePath.lastIndexOf("/") + 1)
}

function SurfaceIcon({ surface }: { surface: RightPanelSurface }) {
  if (surface.kind === "diff") return <FileDiff className="size-3" />
  if (surface.kind === "files") return <Files className="size-3" />
  if (surface.kind === "file") return <File className="size-3" />
  if (surface.kind === "preview") return <Globe2 className="size-3" />
  if (surface.kind === "terminal") return <SquareTerminal className="size-3" />
  return <GitPullRequest className="size-3" />
}

function ResizeHandle({
  width,
  onResize,
}: {
  width: number
  onResize: (width: number, commit: boolean) => void
}) {
  const start = useRef<{ x: number; width: number } | null>(null)
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      className="absolute top-0 -left-1 z-20 h-full w-2 cursor-col-resize touch-none after:absolute after:inset-y-0 after:left-1/2 after:w-px after:bg-transparent hover:after:bg-border"
      onPointerDown={(event) => {
        event.preventDefault()
        start.current = { x: event.clientX, width }
        event.currentTarget.setPointerCapture(event.pointerId)
        document.body.style.cursor = "col-resize"
        document.body.style.userSelect = "none"
      }}
      onPointerMove={(event) => {
        if (start.current)
          onResize(
            start.current.width - (event.clientX - start.current.x),
            false
          )
      }}
      onPointerUp={(event) => {
        if (!start.current) return
        onResize(start.current.width - (event.clientX - start.current.x), true)
        start.current = null
        document.body.style.cursor = ""
        document.body.style.userSelect = ""
        event.currentTarget.releasePointerCapture(event.pointerId)
      }}
    />
  )
}

function Launcher({
  items,
  onOpen,
}: {
  items: ReadonlyArray<RightPanelLauncherItem>
  onOpen: (kind: RightPanelLauncherKind) => void
}) {
  const [highlight, setHighlight] = useState(-1)
  const availableItems = items.filter((item) => item.available)
  const highlightIndex =
    availableItems.length === 0
      ? -1
      : Math.min(highlight, availableItems.length - 1)
  const itemsRef = useRef(availableItems)
  useEffect(() => {
    itemsRef.current = availableItems
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        target?.matches("input, textarea, select, [contenteditable='true']") ||
        document.querySelector(
          '[data-slot="dialog-popup"], [data-slot="menu-popup"], [data-slot="popover-popup"]'
        )
      )
        return
      const item = itemsRef.current.find(
        (candidate) =>
          META[candidate.kind].shortcut.toLowerCase() ===
          event.key.toLowerCase()
      )
      if (!item) return
      event.preventDefault()
      event.stopPropagation()
      onOpen(item.kind)
    }
    window.addEventListener("keydown", onKeyDown, true)
    return () => window.removeEventListener("keydown", onKeyDown, true)
  }, [onOpen])
  const focusOnMount = useCallback((node: HTMLDivElement | null) => {
    node?.focus()
  }, [])
  return (
    <div
      ref={focusOnMount}
      tabIndex={0}
      aria-label="Open a surface"
      className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-6 outline-none"
      onKeyDown={(event) => {
        if (
          event.defaultPrevented ||
          event.metaKey ||
          event.ctrlKey ||
          event.altKey ||
          availableItems.length === 0
        )
          return
        if (event.key === "ArrowDown" || event.key === "ArrowRight") {
          event.preventDefault()
          setHighlight((highlightIndex + 1) % availableItems.length)
        } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
          event.preventDefault()
          setHighlight(
            highlightIndex === -1
              ? availableItems.length - 1
              : (highlightIndex - 1 + availableItems.length) %
                  availableItems.length
          )
        } else if (event.key === "Enter") {
          if ((event.target as HTMLElement).closest("button")) return
          const item = availableItems[highlightIndex]
          if (!item) return
          event.preventDefault()
          onOpen(item.kind)
        }
      }}
    >
      <div className="w-full max-w-lg">
        <div className="mb-5 text-center">
          <h3 className="text-sm font-medium text-foreground">
            Open a surface
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Choose what to show in the right panel.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {items.map((item) => {
            const { kind, available, unavailableHint } = item
            const { label, description, shortcut, Icon } = META[kind]
            const index = availableItems.indexOf(item)
            return available ? (
              <button
                key={kind}
                type="button"
                onClick={() => onOpen(kind)}
                onMouseEnter={() => setHighlight(index)}
                onMouseLeave={() =>
                  setHighlight((current) => (current === index ? -1 : current))
                }
                className={cn(
                  "relative flex min-h-28 flex-col items-start gap-2 rounded-lg border border-border/80 bg-card p-4 text-left transition hover:bg-accent/60",
                  highlightIndex !== -1 &&
                    availableItems[highlightIndex] === item
                    ? "bg-accent/60"
                    : undefined
                )}
              >
                <kbd className="absolute top-3 right-3 text-[10px] text-muted-foreground">
                  {shortcut}
                </kbd>
                <span className="flex items-center gap-2 text-sm font-medium">
                  <Icon className="size-4 text-muted-foreground" />
                  {label}
                </span>
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {description}
                </span>
              </button>
            ) : (
              <div
                key={kind}
                className="relative flex min-h-28 flex-col items-start gap-2 rounded-lg border border-border/80 bg-card p-4 text-left opacity-40"
              >
                <kbd className="absolute top-3 right-3 text-[10px] text-muted-foreground">
                  {shortcut}
                </kbd>
                <span className="flex items-center gap-2 text-sm font-medium">
                  <Icon className="size-4 text-muted-foreground" />
                  {label}
                </span>
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {unavailableHint}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function Control({
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
        type="button"
        aria-label={label}
        onClick={onClick}
        className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        {children}
      </TooltipTrigger>
      <TooltipPopup>{label}</TooltipPopup>
    </Tooltip>
  )
}

export function RightPanelShell({
  surfaces,
  activeSurfaceId,
  terminalLabels,
  launcherItems,
  collapsed,
  onCollapsedChange,
  onActivate,
  onClose,
  onCloseOthers,
  onCloseToRight,
  onCloseAll,
  onOpen,
  children,
}: {
  surfaces: ReadonlyArray<RightPanelSurface>
  activeSurfaceId: string | null
  terminalLabels: ReadonlyMap<string, string>
  launcherItems: ReadonlyArray<RightPanelLauncherItem>
  collapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void
  onActivate: (surface: RightPanelSurface) => void
  onClose: (surface: RightPanelSurface) => void | Promise<void>
  onCloseOthers: (surface: RightPanelSurface) => void | Promise<void>
  onCloseToRight: (surface: RightPanelSurface) => void | Promise<void>
  onCloseAll: () => void | Promise<void>
  onOpen: (kind: RightPanelLauncherKind) => void
  children: React.ReactNode
}) {
  const [width, setWidth] = useState(readWidth)
  const [maximized, setMaximized] = useState(false)
  const [tabMenu, setTabMenu] = useState<{
    surface: RightPanelSurface
    x: number
    y: number
  } | null>(null)
  const isMobile = useIsMobile()
  const overlay = maximized || isMobile
  const tabsRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const resize = useCallback((next: number, commit: boolean) => {
    const available = panelRef.current?.parentElement?.clientWidth
    const clamped = clampWidth(next, available)
    setWidth(clamped)
    if (commit)
      window.localStorage.setItem(PANEL_STORAGE_WIDTH, String(clamped))
  }, [])
  useEffect(() => {
    const active = tabsRef.current?.querySelector<HTMLElement>(
      "[data-active-tab='true']"
    )
    active?.scrollIntoView({ block: "nearest", inline: "nearest" })
  }, [activeSurfaceId])
  useEffect(() => {
    if (!tabMenu) return
    const close = () => setTabMenu(null)
    window.addEventListener("pointerdown", close)
    window.addEventListener("blur", close)
    return () => {
      window.removeEventListener("pointerdown", close)
      window.removeEventListener("blur", close)
    }
  }, [tabMenu])
  if (collapsed) {
    return (
      <button
        type="button"
        aria-label="Show panel"
        onClick={() => onCollapsedChange(false)}
        className="fixed top-2 right-2 z-30 flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <SidebarSimpleIcon className="size-4" />
      </button>
    )
  }
  return (
    <aside
      ref={panelRef}
      data-right-panel
      className={cn(
        "relative flex shrink-0 flex-col bg-background",
        overlay ? "fixed inset-0 !w-full" : "h-full border-l border-border"
      )}
      style={overlay ? { zIndex: Z.MODAL } : { width }}
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-border px-2">
        <div
          ref={tabsRef}
          className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
        >
          {surfaces.map((surface) => {
            const active = surface.id === activeSurfaceId
            const title = titleOf(surface, terminalLabels)
            return (
              <div
                key={surface.id}
                data-active-tab={active}
                className={cn(
                  "group flex h-6 max-w-36 shrink-0 cursor-pointer items-center gap-0.5 rounded-md pr-2 pl-1.5 text-xs",
                  active
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                )}
                onAuxClick={(event) => {
                  if (event.button === 1) void onClose(surface)
                }}
                onContextMenu={(event) => {
                  event.preventDefault()
                  setTabMenu({ surface, x: event.clientX, y: event.clientY })
                }}
              >
                <button
                  type="button"
                  aria-label={`Close ${title}`}
                  onClick={() => void onClose(surface)}
                  className="flex size-4 shrink-0 items-center justify-center rounded-sm hover:bg-muted"
                >
                  <span className="group-hover:hidden">
                    <SurfaceIcon surface={surface} />
                  </span>
                  <X className="hidden size-3 group-hover:block" />
                </button>
                <button
                  type="button"
                  title={title}
                  onClick={() => onActivate(surface)}
                  className="min-w-0 truncate"
                >
                  {title}
                </button>
              </div>
            )
          })}
          {surfaces.length > 0 && (
            <Menu>
              <MenuTrigger
                aria-label="Add panel surface"
                className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <Plus className="size-4" />
              </MenuTrigger>
              <MenuPopup align="start" className="w-48">
                {launcherItems.map((item) => {
                  const { kind, available, disabledReason } = item
                  const { label, shortcut, Icon } = META[kind]
                  return (
                    <MenuItem
                      key={kind}
                      disabled={!available}
                      title={available ? undefined : disabledReason}
                      onClick={() => onOpen(kind)}
                    >
                      <Icon />
                      {label}
                      <span className="ml-auto text-muted-foreground">
                        {shortcut}
                      </span>
                    </MenuItem>
                  )
                })}
              </MenuPopup>
            </Menu>
          )}
        </div>
        {!isMobile && (
          <Control
            label={maximized ? "Exit full screen" : "Expand panel"}
            onClick={() => setMaximized((value) => !value)}
          >
            {maximized ? (
              <ArrowsInIcon className="size-4" />
            ) : (
              <ArrowsOutIcon className="size-4" />
            )}
          </Control>
        )}
        <Control
          label="Hide panel"
          onClick={() => {
            setMaximized(false)
            onCollapsedChange(true)
          }}
        >
          <SidebarSimpleIcon className="size-4" />
        </Control>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {activeSurfaceId === null ? (
          <Launcher items={launcherItems} onOpen={onOpen} />
        ) : (
          children
        )}
      </div>
      {!overlay && <ResizeHandle width={width} onResize={resize} />}
      {tabMenu && (
        <div
          role="menu"
          className="fixed z-[1000] min-w-40 rounded-md border border-border bg-popover p-1 text-xs shadow-lg"
          style={{ left: tabMenu.x, top: tabMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          {[
            { label: "Close", action: () => onClose(tabMenu.surface) },
            {
              label: "Close others",
              action: () => onCloseOthers(tabMenu.surface),
            },
            {
              label: "Close to the right",
              action: () => onCloseToRight(tabMenu.surface),
            },
            { label: "Close all", action: onCloseAll },
          ].map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setTabMenu(null)
                void item.action()
              }}
              className="flex w-full rounded px-2 py-1.5 text-left hover:bg-accent"
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}
