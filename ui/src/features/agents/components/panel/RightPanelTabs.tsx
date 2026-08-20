import {
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import {
  Bot,
  FileDiff,
  FileIcon,
  Files,
  GitPullRequest,
  Globe2,
  Plus,
  TerminalSquare,
  X,
} from "lucide-react"

import type { RightPanelSurface } from "@/features/agents/lib/rightPanelStore"
import type { AgentPullRequest } from "@/features/agents/lib/types"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import {
  Menu,
  MenuItem,
  MenuPopup,
  MenuShortcut,
  MenuTrigger,
} from "@/components/ui/menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import {
  RightPanelShell,
  type RightPanelMode,
} from "@/features/agents/components/panel/RightPanelShell"
import { cn } from "@/lib/utils"

interface RightPanelTabsProps {
  mode: RightPanelMode
  maximized?: boolean
  /** Forwarded to RightPanelShell so this surface persists its own width. */
  widthStorageKey?: string
  /** Forwarded to RightPanelShell as the initial width before a user resize. */
  defaultWidth?: number
  layoutControls?: ReactNode
  surfaces: ReadonlyArray<RightPanelSurface>
  activeSurfaceId: string | null
  pendingSurfaceIds: ReadonlySet<string>
  terminalLabelsById: ReadonlyMap<string, string>
  onActivate: (surface: RightPanelSurface) => void
  onCloseSurface: (surface: RightPanelSurface) => void
  onCloseOtherSurfaces: (surface: RightPanelSurface) => void
  onCloseSurfacesToRight: (surface: RightPanelSurface) => void
  onCloseAllSurfaces: () => void
  onCopyFilePath: (relativePath: string) => void
  onAddTerminal: () => void
  onAddDiff: () => void
  onAddPullRequest: () => void
  terminalAvailable: boolean
  diffAvailable: boolean
  pullRequestAvailable: boolean
  pullRequestStatuses?: Readonly<Record<string, PullRequestTabStatus>>
  children: ReactNode
}

export interface PullRequestTabStatus {
  repository: string
  number: number
  state: AgentPullRequest["state"]
}

const SURFACE_DISABLED_REASONS = {
  terminal: "Terminals are only available from a running workspace.",
  diff: "Changes are only available for threads with a repository.",
  pullRequest: "This thread's branch has no pull request yet.",
} as const

/** Overlays that must win over the launcher's letter shortcuts. */
const LAUNCHER_SHORTCUT_BLOCKING_LAYERS = [
  '[data-slot="dialog-popup"]',
  '[data-slot="alert-dialog-popup"]',
  '[data-slot="menu-popup"]',
  '[data-slot="select-popup"]',
  '[data-slot="popover-popup"]',
  '[data-slot="combobox-popup"]',
].join(",")

/** One-line unavailability hints for the empty-state cards. */
const SURFACE_UNAVAILABLE_HINTS = {
  terminal: "Available once the workspace is running.",
  diff: "Available for Git repositories.",
  pullRequest: "No pull request on this branch yet.",
} as const

type SurfaceShortcutEvent = Pick<
  KeyboardEvent,
  "altKey" | "ctrlKey" | "defaultPrevented" | "isComposing" | "key" | "metaKey"
>

export function surfaceShortcutActionForKey<
  const Action extends { available: boolean; shortcut: string },
>(actions: ReadonlyArray<Action>, event: SurfaceShortcutEvent): Action | null {
  if (event.defaultPrevented || event.isComposing) return null
  if (event.metaKey || event.ctrlKey || event.altKey) return null
  return (
    actions.find(
      (action) =>
        action.available &&
        action.shortcut.toLowerCase() === event.key.toLowerCase()
    ) ?? null
  )
}

function DisabledReasonTooltip(props: {
  reason: string
  trigger: ReactElement
}) {
  return (
    <Tooltip>
      <TooltipTrigger render={props.trigger} />
      <TooltipPopup side="top">{props.reason}</TooltipPopup>
    </Tooltip>
  )
}

function SurfaceMenuItem(props: {
  available: boolean
  disabledReason?: string
  shortcut: string
  onClick: () => void
  children: ReactNode
}) {
  const item = (
    <MenuItem
      className={
        !props.available ? "data-disabled:pointer-events-auto" : undefined
      }
      onClick={props.onClick}
      disabled={!props.available}
      aria-keyshortcuts={props.shortcut}
    >
      {props.children}
      <MenuShortcut>{props.shortcut}</MenuShortcut>
    </MenuItem>
  )
  if (props.available || !props.disabledReason) return item
  return <DisabledReasonTooltip reason={props.disabledReason} trigger={item} />
}

/**
 * Card launcher shown when the right panel has no surfaces. Keyboard-first
 * without palette chrome: a surface's letter opens it directly from anywhere
 * outside a typing context, and arrows plus Enter work while the launcher is
 * focused. The highlight only appears on hover or arrow use. Unavailable
 * surfaces stay visible with a one-line reason.
 */
function RightPanelEmptyState(props: {
  onAddTerminal: () => void
  onAddDiff: () => void
  onAddPullRequest: () => void
  terminalAvailable: boolean
  diffAvailable: boolean
  pullRequestAvailable: boolean
}) {
  // -1 means no highlight: it only appears on hover or arrow use.
  const [highlight, setHighlight] = useState(-1)

  const actions = [
    {
      label: "Terminal",
      description: "Start a shell in this workspace.",
      icon: TerminalSquare,
      shortcut: "T",
      available: props.terminalAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.terminal,
      onClick: props.onAddTerminal,
    },
    {
      label: "Changes",
      description: "Review changes in this thread.",
      icon: FileDiff,
      shortcut: "D",
      available: props.diffAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.diff,
      onClick: props.onAddDiff,
    },
    {
      label: "Pull request",
      description: "Open this branch's pull request.",
      icon: GitPullRequest,
      shortcut: "P",
      available: props.pullRequestAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.pullRequest,
      onClick: props.onAddPullRequest,
    },
  ] as const

  type SurfaceAction = (typeof actions)[number]

  const availableActions = actions.filter((action) => action.available)
  const highlightIndex =
    availableActions.length === 0
      ? -1
      : Math.min(highlight, availableActions.length - 1)

  // Letter shortcuts work while the launcher is visible, not only while it is
  // focused; focus moves around too easily (stray clicks) to carry them.
  // Capture phase so app-level key handlers cannot swallow the event first;
  // typing contexts and already-handled events are left alone.
  const shortcutActionsRef = useRef(availableActions)
  useEffect(() => {
    shortcutActionsRef.current = availableActions
  })
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const action = surfaceShortcutActionForKey(
        shortcutActionsRef.current,
        event
      )
      if (!action) return
      if (document.querySelector(LAUNCHER_SHORTCUT_BLOCKING_LAYERS)) return
      const target = event.target
      if (target instanceof HTMLElement) {
        if (target.closest("input, textarea, select")) return
        // An empty contenteditable (the chat composer at rest) does not count
        // as typing; letters only become text once a draft exists.
        const editable = target.isContentEditable
          ? target
          : target.closest("[contenteditable]")
        if (editable && (editable.textContent ?? "").trim().length > 0) return
      }
      event.preventDefault()
      event.stopPropagation()
      action.onClick()
    }
    window.addEventListener("keydown", handler, true)
    return () => window.removeEventListener("keydown", handler, true)
  }, [])

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (
      event.defaultPrevented ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey
    )
      return
    if (availableActions.length === 0) return
    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      event.preventDefault()
      setHighlight((highlightIndex + 1) % availableActions.length)
      return
    }
    if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      event.preventDefault()
      setHighlight(
        highlightIndex === -1
          ? availableActions.length - 1
          : (highlightIndex - 1 + availableActions.length) %
              availableActions.length
      )
      return
    }
    if (event.key === "Enter") {
      // A focused card button owns its own activation; only open from the
      // highlight when the container itself has focus.
      if (event.target instanceof HTMLElement && event.target.closest("button"))
        return
      const action = availableActions[highlightIndex]
      if (!action) return
      event.preventDefault()
      action.onClick()
    }
  }

  // Stable identity so React only runs this callback ref on mount/unmount; an
  // inline arrow would re-attach and re-focus on every render.
  const focusOnMount = useCallback((node: HTMLDivElement | null) => {
    node?.focus()
  }, [])

  const isHighlighted = (action: SurfaceAction) =>
    highlightIndex !== -1 && availableActions[highlightIndex] === action

  const actionIcon = (action: SurfaceAction, iconClassName = "size-4") => {
    const Icon = action.icon
    return <Icon className={cn("shrink-0", iconClassName)} />
  }

  const cardShellClass =
    "rounded-lg border border-border/80 bg-card dark:border-transparent dark:shadow-none dark:inset-ring-1 dark:inset-ring-white/5"
  const highlightedCardClass = "bg-accent/60 dark:inset-ring-white/20"

  return (
    <div
      ref={focusOnMount}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      aria-label="Open a surface"
      data-surface-launcher-keys={availableActions
        .map((action) => action.shortcut)
        .join("")}
      className={cn(
        "flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-6 pt-6 outline-none",
        // The panel topbar sits above this container; matching bottom padding
        // keeps the cards centered against the full panel, not the leftover.
        "pb-[calc(var(--workspace-topbar-height)+--spacing(6))]"
      )}
    >
      <div className="relative w-full max-w-lg">
        <div className="absolute inset-x-0 bottom-full mb-5 text-center">
          <h3 className="text-sm font-medium text-foreground">
            Open a surface
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Choose what to show in the right panel.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {actions.map((action) =>
            action.available ? (
              <button
                key={action.label}
                type="button"
                onClick={action.onClick}
                onMouseEnter={() =>
                  setHighlight(availableActions.indexOf(action))
                }
                onMouseLeave={() =>
                  setHighlight((current) =>
                    current === availableActions.indexOf(action) ? -1 : current
                  )
                }
                className={cn(
                  "relative flex w-full cursor-pointer flex-col items-start p-4 text-left transition hover:border-border hover:bg-accent/60",
                  cardShellClass,
                  isHighlighted(action) && highlightedCardClass
                )}
              >
                <Kbd className="absolute top-3 right-3">{action.shortcut}</Kbd>
                <span className="flex items-center gap-2 pe-8">
                  {actionIcon(action)}
                  <span className="text-sm font-medium">{action.label}</span>
                </span>
                <span className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                  {action.description}
                </span>
              </button>
            ) : (
              <div
                key={action.label}
                className={cn(
                  "relative flex w-full flex-col items-start p-4 opacity-40",
                  cardShellClass
                )}
              >
                <Kbd className="absolute top-3 right-3">{action.shortcut}</Kbd>
                <span className="flex items-center gap-2 pe-8">
                  {actionIcon(action)}
                  <span className="text-sm font-medium">{action.label}</span>
                </span>
                <span className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                  {action.disabledReason}
                </span>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  )
}

export function surfaceTitle(
  surface: RightPanelSurface,
  terminalLabelsById: ReadonlyMap<string, string>
): string {
  switch (surface.kind) {
    case "diff":
      return "Changes"
    case "files":
      return "Files"
    case "file":
      return surface.relativePath.slice(
        surface.relativePath.lastIndexOf("/") + 1
      )
    case "terminal":
      return terminalLabelsById.get(surface.resourceId) ?? "Terminal"
    case "pull-request":
      return `#${surface.number}`
    case "agents":
      return "Agents"
    case "preview":
      return "Browser"
  }
}

function SurfaceIcon({
  surface,
  pullRequestStatuses,
}: {
  surface: RightPanelSurface
  pullRequestStatuses:
    Readonly<Record<string, PullRequestTabStatus>> | undefined
}) {
  switch (surface.kind) {
    case "preview":
      return <Globe2 className="size-3 shrink-0" />
    case "diff":
      return <FileDiff className="size-3 shrink-0" />
    case "files":
      return <Files className="size-3 shrink-0" />
    case "file":
      return <FileIcon className="size-3 shrink-0" />
    case "terminal":
      return <TerminalSquare className="size-3 shrink-0" />
    case "agents":
      return <Bot className="size-3 shrink-0" />
    case "pull-request": {
      const status = pullRequestStatuses?.[surface.id] ?? null
      const toneClassName =
        status?.state === "merged"
          ? "text-violet-600 dark:text-violet-300/90"
          : status?.state === "closed"
            ? "text-red-600 dark:text-red-300/90"
            : status?.state === "draft"
              ? "text-zinc-500 dark:text-zinc-400/80"
              : status?.state === "open"
                ? "text-emerald-600 dark:text-emerald-300/90"
                : "text-muted-foreground"
      return <GitPullRequest className={cn("size-3 shrink-0", toneClassName)} />
    }
  }
}

type TabContextMenuState = {
  surface: RightPanelSurface
  x: number
  y: number
}

export function RightPanelTabs(props: RightPanelTabsProps) {
  const tabListRef = useRef<HTMLDivElement>(null)
  const [addSurfaceMenuOpen, setAddSurfaceMenuOpen] = useState(false)
  const [tabMenu, setTabMenu] = useState<TabContextMenuState | null>(null)

  const addSurfaceActions = [
    {
      label: "Terminal",
      icon: TerminalSquare,
      shortcut: "T",
      available: props.terminalAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.terminal,
      onClick: props.onAddTerminal,
    },
    {
      label: "Changes",
      icon: FileDiff,
      shortcut: "D",
      available: props.diffAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.diff,
      onClick: props.onAddDiff,
    },
    {
      label: "Pull request",
      icon: GitPullRequest,
      shortcut: "P",
      available: props.pullRequestAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.pullRequest,
      onClick: props.onAddPullRequest,
    },
  ] as const

  const handleAddSurfaceMenuKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement>
  ) => {
    const action = surfaceShortcutActionForKey(
      addSurfaceActions,
      event.nativeEvent
    )
    if (!action) return
    event.preventDefault()
    event.stopPropagation()
    setAddSurfaceMenuOpen(false)
    action.onClick()
  }

  const handleTabContextMenu = useCallback(
    (event: ReactMouseEvent, surface: RightPanelSurface) => {
      event.preventDefault()
      event.stopPropagation()
      setTabMenu({ surface, x: event.clientX, y: event.clientY })
    },
    []
  )
  const handleTabMouseDown = useCallback((event: ReactMouseEvent) => {
    if (event.button !== 1) return
    event.preventDefault()
  }, [])
  const handleTabAuxClick = useCallback(
    (event: ReactMouseEvent, surface: RightPanelSurface) => {
      if (event.button !== 1) return
      event.preventDefault()
      event.stopPropagation()
      props.onCloseSurface(surface)
    },
    [props]
  )

  useEffect(() => {
    const activeTab = tabListRef.current?.querySelector<HTMLElement>(
      "[data-active-tab='true']"
    )
    activeTab?.scrollIntoView({ block: "nearest", inline: "nearest" })
  }, [props.activeSurfaceId])

  const menuSurfaceIndex = tabMenu
    ? props.surfaces.findIndex((entry) => entry.id === tabMenu.surface.id)
    : -1

  return (
    <RightPanelShell
      mode={props.mode}
      {...(props.maximized !== undefined ? { maximized: props.maximized } : {})}
      {...(props.widthStorageKey !== undefined
        ? { widthStorageKey: props.widthStorageKey }
        : {})}
      {...(props.defaultWidth !== undefined
        ? { defaultWidth: props.defaultWidth }
        : {})}
    >
      <div
        className={cn(
          "flex h-[var(--workspace-topbar-height)] min-h-[var(--workspace-topbar-height)] shrink-0 items-center gap-1 pl-2",
          props.layoutControls ? "pr-3" : "pr-2"
        )}
        data-right-panel-tabbar
      >
        <ScrollArea
          ref={tabListRef}
          hideScrollbars
          scrollFade
          className="min-w-0 flex-1 rounded-none"
          data-right-panel-tab-list
        >
          <div className="flex h-full w-max min-w-full items-center gap-1">
            {props.surfaces.map((surface) => {
              const active = surface.id === props.activeSurfaceId
              const pending = props.pendingSurfaceIds.has(surface.id)
              const title = surfaceTitle(surface, props.terminalLabelsById)
              return (
                <div
                  key={surface.id}
                  data-active-tab={active}
                  onMouseDown={handleTabMouseDown}
                  onAuxClick={(event) => handleTabAuxClick(event, surface)}
                  onContextMenu={(event) =>
                    handleTabContextMenu(event, surface)
                  }
                  className={cn(
                    "group/tab flex h-6 max-w-36 shrink-0 cursor-pointer items-center gap-0.5 rounded-md pr-2 pl-1.5 text-xs",
                    active
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                  )}
                >
                  <button
                    type="button"
                    className="group/close relative flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-sm hover:bg-muted"
                    aria-label={`Close ${title}`}
                    onClick={() => props.onCloseSurface(surface)}
                  >
                    <span className="relative flex size-3 items-center justify-center group-hover/tab:hidden group-focus-visible/close:hidden">
                      <SurfaceIcon
                        surface={surface}
                        pullRequestStatuses={props.pullRequestStatuses}
                      />
                      {pending ? (
                        <span
                          className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-current"
                          aria-hidden
                        />
                      ) : null}
                    </span>
                    <X className="hidden size-3 group-hover/tab:block group-focus-visible/close:block" />
                  </button>
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <button
                          type="button"
                          className="flex min-w-0 cursor-pointer items-center"
                          onClick={() => props.onActivate(surface)}
                        >
                          <span className="truncate">{title}</span>
                        </button>
                      }
                    />
                    <TooltipPopup>{title}</TooltipPopup>
                  </Tooltip>
                </div>
              )
            })}
            {props.surfaces.length > 0 ? (
              <Menu
                open={addSurfaceMenuOpen}
                onOpenChange={setAddSurfaceMenuOpen}
              >
                <MenuTrigger
                  render={
                    <Button
                      aria-label="Add panel surface"
                      className="size-6 shrink-0 text-muted-foreground hover:text-foreground"
                      size="icon-xs"
                      variant="ghost"
                    />
                  }
                >
                  <Plus className="size-3.5" />
                </MenuTrigger>
                <MenuPopup
                  align="start"
                  side="bottom"
                  sideOffset={6}
                  className="min-w-44"
                  onKeyDownCapture={handleAddSurfaceMenuKeyDown}
                >
                  {addSurfaceActions.map((action) => {
                    const Icon = action.icon
                    return (
                      <SurfaceMenuItem
                        key={action.label}
                        available={action.available}
                        disabledReason={action.disabledReason}
                        shortcut={action.shortcut}
                        onClick={action.onClick}
                      >
                        <Icon />
                        {action.label}
                      </SurfaceMenuItem>
                    )
                  })}
                </MenuPopup>
              </Menu>
            ) : null}
          </div>
        </ScrollArea>
        {props.layoutControls}
      </div>
      {tabMenu ? (
        <Menu
          open
          onOpenChange={(open) => {
            if (!open) setTabMenu(null)
          }}
        >
          <MenuPopup
            align="start"
            side="bottom"
            sideOffset={0}
            className="min-w-40"
            anchor={{
              getBoundingClientRect: () =>
                new DOMRect(tabMenu.x, tabMenu.y, 0, 0),
            }}
          >
            {tabMenu.surface.kind === "file" ? (
              <MenuItem
                onClick={() => {
                  if (tabMenu.surface.kind === "file") {
                    props.onCopyFilePath(tabMenu.surface.relativePath)
                  }
                }}
              >
                Copy path
              </MenuItem>
            ) : null}
            <MenuItem onClick={() => props.onCloseSurface(tabMenu.surface)}>
              Close
            </MenuItem>
            <MenuItem
              disabled={props.surfaces.length <= 1}
              onClick={() => props.onCloseOtherSurfaces(tabMenu.surface)}
            >
              Close others
            </MenuItem>
            <MenuItem
              disabled={menuSurfaceIndex >= props.surfaces.length - 1}
              onClick={() => props.onCloseSurfacesToRight(tabMenu.surface)}
            >
              Close to the right
            </MenuItem>
            <MenuItem
              disabled={props.surfaces.length === 0}
              onClick={() => props.onCloseAllSurfaces()}
            >
              Close all
            </MenuItem>
          </MenuPopup>
        </Menu>
      ) : null}
      <div
        className="flex min-h-0 flex-1 flex-col"
        data-right-panel-surface-content
      >
        {props.activeSurfaceId === null ? (
          <RightPanelEmptyState
            onAddTerminal={props.onAddTerminal}
            onAddDiff={props.onAddDiff}
            onAddPullRequest={props.onAddPullRequest}
            terminalAvailable={props.terminalAvailable}
            diffAvailable={props.diffAvailable}
            pullRequestAvailable={props.pullRequestAvailable}
          />
        ) : (
          props.children
        )}
      </div>
    </RightPanelShell>
  )
}
