import { useCallback, useEffect, useRef, useState } from "react"
import type {
  ReactElement,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from "react"

import type { Glyph } from "@/components/glyphs"
import type { RightPanelSurface } from "@/features/agents/lib/rightPanelStore"
import type { RightPanelMode } from "@/features/agents/components/panel/RightPanelShell"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/context-menu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Kbd } from "@langchain/gtm-platform-design-system/ui/kbd"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import {
  Bot,
  File,
  FileEdit,
  Folder,
  Globe,
  Plus,
  Terminal,
  X,
} from "@/components/glyphs"
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

function DisabledReasonTooltip(props: {
  reason: string
  trigger: ReactElement
}) {
  return (
    <Tooltip>
      <TooltipTrigger render={props.trigger} />
      <TooltipContent side="top">{props.reason}</TooltipContent>
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
    <DropdownMenuItem
      className={
        !props.available ? "data-disabled:pointer-events-auto" : undefined
      }
      onClick={props.onClick}
      disabled={!props.available}
      aria-keyshortcuts={props.shortcut}
    >
      {props.children}
      <DropdownMenuShortcut>{props.shortcut}</DropdownMenuShortcut>
    </DropdownMenuItem>
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
      icon: Terminal,
      shortcut: "T",
      available: props.terminalAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.terminal,
      onClick: props.onAddTerminal,
    },
    {
      label: "Changes",
      description: "Review changes in this thread.",
      icon: FileEdit,
      shortcut: "D",
      available: props.diffAvailable,
      disabledReason: SURFACE_UNAVAILABLE_HINTS.diff,
      onClick: props.onAddDiff,
    },
    {
      label: "Files",
      description: "Browse files in this workspace.",
      icon: Folder,
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
    node?.focus()
  }, [])

  const isHighlighted = (action: SurfaceAction) =>
    highlightIndex !== -1 && availableActions[highlightIndex] === action

  const card = (action: SurfaceAction, detail: string) => (
    <>
      <Kbd className="absolute top-3 right-3">{action.shortcut}</Kbd>
      <Inline gap="sm" className="pe-8">
        <Icon icon={action.icon} size="md" className="text-ink-subtle" />
        <span className="text-label font-medium text-ink">{action.label}</span>
      </Inline>
      <span className="text-meta text-ink-subtle">{detail}</span>
    </>
  )

  return (
    <div
      ref={focusOnMount}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      aria-label="Open a surface"
      data-surface-launcher-keys={availableActions
        .map((action) => action.shortcut)
        .join("")}
      // Bottom air of one toolbar keeps the launcher centred on the whole
      // panel rather than on what is left under the tab band.
      className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-toolbar outline-none"
    >
      <EmptyState
        title="Open a surface"
        description="Choose what to show in the right panel."
        action={
          <div className="grid w-full grid-cols-2 gap-2">
            {actions.map((action) =>
              action.available ? (
                <Stack
                  key={action.label}
                  render={<button type="button" />}
                  gap="xs"
                  align="start"
                  padding="md"
                  radius="control"
                  border="line"
                  bg={isHighlighted(action) ? "hover" : "panel"}
                  onClick={action.onClick}
                  onMouseEnter={() =>
                    setHighlight(availableActions.indexOf(action))
                  }
                  onMouseLeave={() =>
                    setHighlight((current) =>
                      current === availableActions.indexOf(action)
                        ? -1
                        : current
                    )
                  }
                  className="relative w-full cursor-pointer text-left transition-colors duration-fast ease-out-quint hover:bg-hover motion-reduce:transition-none"
                >
                  {card(action, action.description)}
                </Stack>
              ) : (
                <Stack
                  key={action.label}
                  gap="xs"
                  align="start"
                  padding="md"
                  radius="control"
                  border="line"
                  className="relative w-full text-left opacity-50"
                >
                  {card(action, action.disabledReason)}
                </Stack>
              )
            )}
          </div>
        }
      />
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

const SURFACE_GLYPH: Record<RightPanelSurface["kind"], Glyph> = {
  preview: Globe,
  diff: FileEdit,
  files: Folder,
  file: File,
  terminal: Terminal,
  agents: Bot,
}

/**
 * One closable document tab. The panel's tabs are documents the user opens and
 * closes (terminals, files), not a fixed view switch, so they are drawn on the
 * system Tabs trigger geometry rather than mounted as `Tabs`: a tab carries its
 * own close control, a context menu, and middle-click close.
 */
function SurfaceTab(props: {
  surface: RightPanelSurface
  title: string
  active: boolean
  pending: boolean
  index: number
  count: number
  onActivate: () => void
  onClose: () => void
  onCloseOthers: () => void
  onCloseToRight: () => void
  onCloseAll: () => void
  onCopyPath: (relativePath: string) => void
}) {
  const { surface } = props
  return (
    <ContextMenu>
      <ContextMenuTrigger
        render={
          <div
            data-active-tab={props.active}
            onMouseDown={(event: ReactMouseEvent) => {
              if (event.button === 1) event.preventDefault()
            }}
            onAuxClick={(event: ReactMouseEvent) => {
              if (event.button !== 1) return
              event.preventDefault()
              event.stopPropagation()
              props.onClose()
            }}
            className={cn(
              "group/tab flex h-control-sm max-w-40 shrink-0 cursor-pointer items-center gap-1 rounded-compact border pr-2.5 pl-1 text-label font-medium transition-[color,background-color,border-color] duration-fast ease-out-quint motion-reduce:transition-none",
              props.active
                ? "border-line-strong bg-panel text-ink"
                : "border-transparent text-ink-subtle hover:bg-hover hover:text-ink"
            )}
          />
        }
      >
        <button
          type="button"
          className="group/close relative flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-badge outline-none hover:bg-selected focus-visible:ring-2 focus-visible:ring-primary"
          aria-label={`Close ${props.title}`}
          onClick={props.onClose}
        >
          <span className="relative flex items-center justify-center group-hover/tab:hidden group-focus-visible/close:hidden">
            <Icon icon={SURFACE_GLYPH[surface.kind]} size="sm" />
            {props.pending ? (
              <span
                className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-current"
                aria-hidden
              />
            ) : null}
          </span>
          <Icon
            icon={X}
            size="sm"
            className="hidden group-hover/tab:block group-focus-visible/close:block"
          />
        </button>
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                className="flex min-w-0 cursor-pointer items-center outline-none focus-visible:underline"
                onClick={props.onActivate}
              >
                <span className="truncate">{props.title}</span>
              </button>
            }
          />
          <TooltipContent>{props.title}</TooltipContent>
        </Tooltip>
      </ContextMenuTrigger>
      <ContextMenuContent className="min-w-40">
        {surface.kind === "file" ? (
          <>
            <ContextMenuItem
              onClick={() => props.onCopyPath(surface.relativePath)}
            >
              Copy path
            </ContextMenuItem>
            <DropdownMenuSeparator />
          </>
        ) : null}
        <ContextMenuItem onClick={props.onClose}>Close</ContextMenuItem>
        <ContextMenuItem
          disabled={props.count <= 1}
          onClick={props.onCloseOthers}
        >
          Close others
        </ContextMenuItem>
        <ContextMenuItem
          disabled={props.index >= props.count - 1}
          onClick={props.onCloseToRight}
        >
          Close to the right
        </ContextMenuItem>
        <ContextMenuItem
          disabled={props.count === 0}
          onClick={props.onCloseAll}
        >
          Close all
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function RightPanelTabs(props: RightPanelTabsProps) {
  const tabListRef = useRef<HTMLDivElement>(null)
  const [addSurfaceMenuOpen, setAddSurfaceMenuOpen] = useState(false)

  const addSurfaceActions = [
    {
      label: "Terminal",
      icon: Terminal,
      shortcut: "T",
      available: props.terminalAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.terminal,
      onClick: props.onAddTerminal,
    },
    {
      label: "Changes",
      icon: FileEdit,
      shortcut: "D",
      available: props.diffAvailable,
      disabledReason: SURFACE_DISABLED_REASONS.diff,
      onClick: props.onAddDiff,
    },
    {
      label: "Files",
      icon: Folder,
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
      <Inline
        gap="xs"
        className="h-toolbar shrink-0 border-b border-line px-2"
        data-right-panel-tabbar
      >
        <ScrollArea
          viewportRef={tabListRef}
          overflow="horizontal"
          className="min-w-0 flex-1"
          data-right-panel-tab-list
        >
          <Inline gap="xs" className="h-toolbar w-max min-w-full">
            {props.surfaces.map((surface, index) => (
              <SurfaceTab
                key={surface.id}
                surface={surface}
                title={surfaceTitle(surface, props.terminalLabelsById)}
                active={surface.id === props.activeSurfaceId}
                pending={props.pendingSurfaceIds.has(surface.id)}
                index={index}
                count={props.surfaces.length}
                onActivate={() => props.onActivate(surface)}
                onClose={() => props.onCloseSurface(surface)}
                onCloseOthers={() => props.onCloseOtherSurfaces(surface)}
                onCloseToRight={() => props.onCloseSurfacesToRight(surface)}
                onCloseAll={() => props.onCloseAllSurfaces()}
                onCopyPath={props.onCopyFilePath}
              />
            ))}
          </Inline>
        </ScrollArea>
        {props.surfaces.length > 0 ? (
          <DropdownMenu
            open={addSurfaceMenuOpen}
            onOpenChange={setAddSurfaceMenuOpen}
          >
            <DropdownMenuTrigger
              render={
                <Button
                  aria-label="Add panel surface"
                  className="text-ink-subtle hover:text-ink"
                  size="icon-sm"
                  variant="ghost"
                />
              }
            >
              <Icon icon={Plus} />
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              className="min-w-44"
              onKeyDownCapture={handleAddSurfaceMenuKeyDown}
            >
              {addSurfaceActions.map((action) => (
                <SurfaceMenuItem
                  key={action.label}
                  available={action.available}
                  disabledReason={action.disabledReason}
                  shortcut={action.shortcut}
                  onClick={action.onClick}
                >
                  <Icon icon={action.icon} size="sm" />
                  {action.label}
                </SurfaceMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
        {props.layoutControls}
      </Inline>
      <Box
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
      </Box>
    </RightPanelShell>
  )
}
