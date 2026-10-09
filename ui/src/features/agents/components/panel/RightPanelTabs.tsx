import {
  GlobeRegularIcon,
  PlusIcon,
  XIcon,
} from "@langchain/macaw-components/icons"
import { useCallback, useEffect, useRef, useState } from "react"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@langchain/macaw-components/ContextMenu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { FileIcon } from "@phosphor-icons/react/dist/ssr/File"
import { FilesIcon } from "@phosphor-icons/react/dist/ssr/Files"
import { GitDiffIcon } from "@phosphor-icons/react/dist/ssr/GitDiff"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { TerminalWindowIcon } from "@phosphor-icons/react/dist/ssr/TerminalWindow"
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from "react"

import type { RightPanelSurface } from "@/features/agents/lib/rightPanelStore"
import type { RightPanelMode } from "@/features/agents/components/panel/RightPanelShell"
import { RightPanelShell } from "@/features/agents/components/panel/RightPanelShell"
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
  onAddFiles: () => void
  terminalAvailable: boolean
  diffAvailable: boolean
  children: ReactNode
}

const SURFACE_DISABLED_REASONS = {
  terminal: "Terminals are only available from a running workspace.",
  diff: "Changes are only available for threads with a repository.",
  files: "Files are only available from a running workspace.",
} as const

/**
 * Overlays that must win over the launcher's letter shortcuts. One that
 * contains the launcher (the narrow-window sheet) does not count.
 */
const LAUNCHER_SHORTCUT_BLOCKING_LAYERS = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[role="menu"]',
  '[role="listbox"]',
].join(",")

function hasLayerAbove(launcher: HTMLElement | null): boolean {
  return Array.from(
    document.querySelectorAll(LAUNCHER_SHORTCUT_BLOCKING_LAYERS)
  ).some((layer) => !launcher || !layer.contains(launcher))
}

/** One-line unavailability hints for the empty-state cards. */
const SURFACE_UNAVAILABLE_HINTS = {
  terminal: "Available once the workspace is running.",
  diff: "Available for Git repositories.",
  files: "Available once the workspace is running.",
} as const

type SurfaceShortcutEvent = Pick<
  KeyboardEvent,
  "altKey" | "ctrlKey" | "defaultPrevented" | "isComposing" | "key" | "metaKey"
>

export function surfaceShortcutActionForKey<
  const TAction extends { available: boolean; shortcut: string },
>(
  actions: ReadonlyArray<TAction>,
  event: SurfaceShortcutEvent
): TAction | null {
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

function SurfaceMenuItem(props: {
  available: boolean
  disabledReason?: string
  shortcut: string
  onSelect: () => void
  children: ReactNode
}) {
  return (
    <Tooltip
      disabled={props.available || !props.disabledReason}
      side="top"
      title={props.disabledReason}
    >
      <DropdownMenuItem
        className="gap-space-2 text-xs data-[disabled]:pointer-events-auto"
        onSelect={props.onSelect}
        disabled={!props.available}
        aria-keyshortcuts={props.shortcut}
        size="sm"
      >
        {props.children}
        <Kbd className="ml-auto">{props.shortcut}</Kbd>
      </DropdownMenuItem>
    </Tooltip>
  )
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
  onAddFiles: () => void
  terminalAvailable: boolean
  diffAvailable: boolean
}) {
  // -1 means no highlight: it only appears on hover or arrow use.
  const [highlight, setHighlight] = useState(-1)

  const actions = [
    {
      label: "Terminal",
      description: "Start a shell in this workspace.",
      icon: TerminalWindowIcon,
      shortcut: "T",
      available: props.terminalAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.terminal,
      onClick: props.onAddTerminal,
    },
    {
      label: "Changes",
      description: "Review changes in this thread.",
      icon: GitDiffIcon,
      shortcut: "D",
      available: props.diffAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.diff,
      onClick: props.onAddDiff,
    },
    {
      label: "Files",
      description: "Browse files in this workspace.",
      icon: FilesIcon,
      shortcut: "F",
      available: props.terminalAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.files,
      onClick: props.onAddFiles,
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
  const launcherRef = useRef<HTMLDivElement | null>(null)
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
      if (hasLayerAbove(launcherRef.current)) return
      const target = event.target
      if (target instanceof HTMLElement) {
        if (target.closest("input, textarea, select")) return
        // Any focused contenteditable is a typing context, empty or not: the
        // chat composer sits beside this launcher, and its first character
        // must reach the editor rather than open a surface.
        if (target.isContentEditable || target.closest("[contenteditable]"))
          return
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
    launcherRef.current = node
    node?.focus()
  }, [])

  const isHighlighted = (action: SurfaceAction) =>
    highlightIndex !== -1 && availableActions[highlightIndex] === action

  const actionIcon = (action: SurfaceAction, iconClassName = "size-4") => {
    const Icon = action.icon
    return <Icon className={cn("shrink-0", iconClassName)} weight="regular" />
  }

  const cardShellClass = "rounded-lg border border-default bg-surface-level-2"
  const highlightedCardClass = "bg-surface-level-2-hover"

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
          <h3 className="text-sm font-medium text-primary">Open a surface</h3>
          <p className="mt-1 text-xs text-secondary">
            Choose what to show in the right panel.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-space-2">
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
                  "relative flex w-full cursor-pointer flex-col items-start p-space-4 text-left transition hover:bg-surface-level-2-hover",
                  cardShellClass,
                  isHighlighted(action) && highlightedCardClass
                )}
              >
                <Kbd className="absolute top-3 right-3">{action.shortcut}</Kbd>
                <span className="flex items-center gap-2 pe-8">
                  {actionIcon(action)}
                  <span className="text-sm font-medium">{action.label}</span>
                </span>
                <span className="mt-1.5 text-xs leading-relaxed text-secondary">
                  {action.description}
                </span>
              </button>
            ) : (
              <div
                key={action.label}
                className={cn(
                  "relative flex w-full flex-col items-start p-space-4 opacity-40",
                  cardShellClass
                )}
              >
                <Kbd className="absolute top-3 right-3">{action.shortcut}</Kbd>
                <span className="flex items-center gap-2 pe-8">
                  {actionIcon(action)}
                  <span className="text-sm font-medium">{action.label}</span>
                </span>
                <span className="mt-1.5 text-xs leading-relaxed text-secondary">
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
    case "agents":
      return "Agents"
    case "preview":
      return "Browser"
  }
}

const SURFACE_ICONS = {
  preview: GlobeRegularIcon,
  diff: GitDiffIcon,
  files: FilesIcon,
  file: FileIcon,
  terminal: TerminalWindowIcon,
  agents: RobotIcon,
} as const satisfies Record<RightPanelSurface["kind"], unknown>

function SurfaceIcon({ surface }: { surface: RightPanelSurface }) {
  const Icon = SURFACE_ICONS[surface.kind]
  return <Icon className="size-3 shrink-0" weight="regular" />
}

export function RightPanelTabs(props: RightPanelTabsProps) {
  const tabListRef = useRef<HTMLDivElement>(null)
  const [addSurfaceMenuOpen, setAddSurfaceMenuOpen] = useState(false)

  const addSurfaceActions = [
    {
      label: "Terminal",
      icon: TerminalWindowIcon,
      shortcut: "T",
      available: props.terminalAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.terminal,
      onClick: props.onAddTerminal,
    },
    {
      label: "Changes",
      icon: GitDiffIcon,
      shortcut: "D",
      available: props.diffAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.diff,
      onClick: props.onAddDiff,
    },
    {
      label: "Files",
      icon: FilesIcon,
      shortcut: "F",
      available: props.terminalAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.files,
      onClick: props.onAddFiles,
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
        <div
          ref={tabListRef}
          aria-label="Panel surfaces"
          className="min-w-0 flex-1 [scrollbar-width:none] overflow-x-auto [&::-webkit-scrollbar]:hidden"
          data-right-panel-tab-list
          role="tablist"
        >
          <div className="flex h-full w-max min-w-full items-center gap-1">
            {props.surfaces.map((surface, index) => {
              const active = surface.id === props.activeSurfaceId
              const pending = props.pendingSurfaceIds.has(surface.id)
              const title = surfaceTitle(surface, props.terminalLabelsById)
              return (
                <ContextMenu key={surface.id}>
                  <ContextMenuTrigger asChild>
                    <div
                      data-active-tab={active}
                      role="presentation"
                      onMouseDown={handleTabMouseDown}
                      onAuxClick={(event) => handleTabAuxClick(event, surface)}
                      onContextMenu={(event) => event.stopPropagation()}
                      className={cn(
                        "group/tab flex h-6 max-w-36 shrink-0 cursor-pointer items-center gap-0.5 rounded-md pr-2 pl-1.5 text-xs",
                        active
                          ? "bg-selected text-primary"
                          : "text-secondary hover:bg-surface-level-1-hover hover:text-primary"
                      )}
                    >
                      <button
                        type="button"
                        className="group/close relative flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-sm hover:bg-surface-level-2"
                        aria-label={`Close ${title}`}
                        onClick={() => props.onCloseSurface(surface)}
                      >
                        <span className="relative flex size-3 items-center justify-center group-hover/tab:hidden group-focus-visible/close:hidden">
                          <SurfaceIcon surface={surface} />
                          {pending ? (
                            <span
                              className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-current"
                              aria-hidden
                            />
                          ) : null}
                        </span>
                        <XIcon
                          className="hidden size-3 group-hover/tab:block group-focus-visible/close:block"
                          weight="bold"
                        />
                      </button>
                      <Tooltip title={title}>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={active}
                          className="flex min-w-0 cursor-pointer items-center"
                          onClick={() => props.onActivate(surface)}
                        >
                          <span className="truncate">{title}</span>
                        </button>
                      </Tooltip>
                    </div>
                  </ContextMenuTrigger>
                  <ContextMenuContent className="min-w-40">
                    {surface.kind === "file" ? (
                      <ContextMenuItem
                        size="sm"
                        onSelect={() =>
                          props.onCopyFilePath(surface.relativePath)
                        }
                      >
                        Copy path
                      </ContextMenuItem>
                    ) : null}
                    <ContextMenuItem
                      size="sm"
                      onSelect={() => props.onCloseSurface(surface)}
                    >
                      Close
                    </ContextMenuItem>
                    <ContextMenuItem
                      size="sm"
                      disabled={props.surfaces.length <= 1}
                      onSelect={() => props.onCloseOtherSurfaces(surface)}
                    >
                      Close others
                    </ContextMenuItem>
                    <ContextMenuItem
                      size="sm"
                      disabled={index >= props.surfaces.length - 1}
                      onSelect={() => props.onCloseSurfacesToRight(surface)}
                    >
                      Close to the right
                    </ContextMenuItem>
                    <ContextMenuItem
                      size="sm"
                      onSelect={() => props.onCloseAllSurfaces()}
                    >
                      Close all
                    </ContextMenuItem>
                  </ContextMenuContent>
                </ContextMenu>
              )
            })}
          </div>
        </div>
        {props.surfaces.length > 0 ? (
          <DropdownMenu
            open={addSurfaceMenuOpen}
            onOpenChange={setAddSurfaceMenuOpen}
          >
            <DropdownMenuTrigger asChild>
              <IconButton
                className="shrink-0"
                color="secondary"
                icon={PlusIcon}
                iconClassName="size-3.5"
                label="Add panel surface"
                size="sm"
                variant="plain"
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent
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
                    onSelect={action.onClick}
                  >
                    <Icon
                      className="size-3.5 shrink-0 text-icon-secondary"
                      weight="regular"
                    />
                    {action.label}
                  </SurfaceMenuItem>
                )
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
        {props.layoutControls}
      </div>
      <div
        className="flex min-h-0 flex-1 flex-col"
        data-right-panel-surface-content
      >
        {props.activeSurfaceId === null ? (
          <RightPanelEmptyState
            onAddTerminal={props.onAddTerminal}
            onAddDiff={props.onAddDiff}
            onAddFiles={props.onAddFiles}
            terminalAvailable={props.terminalAvailable}
            diffAvailable={props.diffAvailable}
          />
        ) : (
          props.children
        )}
      </div>
    </RightPanelShell>
  )
}
