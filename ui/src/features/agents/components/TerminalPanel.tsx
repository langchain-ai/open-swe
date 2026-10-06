import { useEffect, useRef, useState } from "react"

import type {
  GhosttyColor,
  GhosttyTheme,
} from "@/features/agents/terminal/ghostty/core"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import type { TerminalSplitDirection } from "@/features/agents/lib/terminalState"
import { MAX_TERMINALS_PER_GROUP } from "@/features/agents/lib/terminalState"
import { cn } from "@/lib/utils"
import { useAttachedTerminal } from "@/features/agents/lib/terminalSession"
import { GhosttyTerminalSurface } from "@/features/agents/terminal/ghostty/surface"
import { PanelIconButton } from "@/features/agents/components/panel/PanelIconButton"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { POPUP_SURFACE_SHELL } from "@langchain/gtm-platform-design-system/ui/popup-surface"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import {
  Columns,
  Copy,
  Plus,
  RefreshCw,
  Rows,
  Trash2,
  X,
} from "@/components/glyphs"

interface TerminalPanelProps {
  target: TerminalTarget
  cwd: string
  /** The terminal group this tab renders; splits live inside it. */
  groupId: string
  terminals: TerminalGroupsController
  onOpenFile?: (path: string) => void
  onAddToChat?: (text: string) => void
}

interface TerminalViewportProps {
  target: TerminalTarget
  terminalId: string
  cwd: string
  active: boolean
  focusRequest: number
  onFocus: () => void
  onOpenFile?: (path: string) => void
  onAddToChat?: (text: string) => void
  clearRequest: number
  restartRequest: number
}

/** Parses a computed `rgb()`/`rgba()` or `color(srgb …)` value. */
function parseComputedColor(value: string): GhosttyColor {
  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/.exec(value)
  if (rgb) return { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]) }
  const srgb = /^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/.exec(value)
  if (srgb)
    return {
      r: Math.round(Number(srgb[1]) * 255),
      g: Math.round(Number(srgb[2]) * 255),
      b: Math.round(Number(srgb[3]) * 255),
    }
  throw new Error(`Unsupported terminal colour: ${value}`)
}

/**
 * The canvas renderer takes RGB, not CSS, so the theme reads the design
 * tokens through a probe element: the terminal sits on the panel surface in
 * panel ink, and the cursor and selection are the primary.
 */
function terminalTheme(): GhosttyTheme {
  const probe = document.createElement("span")
  probe.style.display = "none"
  document.body.append(probe)
  try {
    const read = (token: string) => {
      probe.style.color = `var(${token})`
      return parseComputedColor(getComputedStyle(probe).color)
    }
    const background = read("--gtm-panel")
    const foreground = read("--gtm-ink")
    const cursor = read("--gtm-primary")
    return {
      background,
      foreground,
      cursor,
      selectionBackground: `rgb(${cursor.r} ${cursor.g} ${cursor.b} / 0.25)`,
    }
  } finally {
    probe.remove()
  }
}

function TerminalViewport({
  target,
  terminalId,
  cwd,
  active,
  focusRequest,
  onFocus,
  onOpenFile,
  onAddToChat,
  clearRequest,
  restartRequest,
}: TerminalViewportProps) {
  const mountRef = useRef<HTMLDivElement>(null)
  const surfaceRef = useRef<GhosttyTerminalSurface | null>(null)
  const previousRef = useRef({ buffer: "", version: 0 })
  const [error, setError] = useState<string | null>(null)
  const [selection, setSelection] = useState<string | null>(null)
  const targetId = target.kind === "local" ? target.sessionId : target.threadId
  const state = useAttachedTerminal(
    target,
    terminalId,
    cwd,
    clearRequest,
    restartRequest
  )
  const latestStateRef = useRef(state)
  useEffect(() => {
    latestStateRef.current = state
  }, [state])
  // Read through refs: naming these as dependencies would tear down and
  // recreate the surface whenever the parent re-renders.
  const onOpenFileRef = useRef(onOpenFile)
  const activeRef = useRef(active)
  useEffect(() => {
    onOpenFileRef.current = onOpenFile
    activeRef.current = active
  }, [onOpenFile, active])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    let disposed = false
    let surface: GhosttyTerminalSurface | null = null

    void GhosttyTerminalSurface.create(mount, {
      theme: terminalTheme(),
      font: {
        family:
          "IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        size: 13,
      },
      onData: (data) => {
        void latestStateRef.current
          .write(data)
          .catch((cause) =>
            setError(
              cause instanceof Error ? cause.message : "Terminal write failed"
            )
          )
      },
      onResize: (cols, rows) => latestStateRef.current.resize(cols, rows),
      onSelectionChange: () => {
        const text = surfaceRef.current?.getSelection().trim() ?? ""
        setSelection(text || null)
      },
      onCopy: (text) => void navigator.clipboard.writeText(text),
      beforeKey: () => true,
      onLinkActivate: (text, event) => {
        if (!(event.metaKey || event.ctrlKey)) return
        if (/^https?:\/\//i.test(text)) {
          if (target.kind === "local")
            void window.openSweDesktop?.openExternal(text)
          else window.open(text, "_blank", "noopener,noreferrer")
          return
        }
        const openFile = onOpenFileRef.current
        if (target.kind !== "local" || !openFile) return
        const path = text.replace(/:\d+(?::\d+)?$/, "")
        void window.openSweDesktop
          ?.resolveLocalProjectPath({ localSessionId: targetId, path })
          .then((relativePath) => {
            if (relativePath) openFile(relativePath)
          })
          .catch(() => {})
      },
    })
      .then((created) => {
        if (disposed) {
          created.dispose()
          return
        }
        surface = created
        surfaceRef.current = created
        const latestState = latestStateRef.current
        previousRef.current = {
          buffer: latestState.buffer,
          version: latestState.version,
        }
        if (latestState.buffer) created.resetAndWrite(latestState.buffer)
        if (activeRef.current) created.focus()
      })
      .catch((cause: unknown) => {
        if (!disposed) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Unable to initialize terminal"
          )
        }
      })

    const observer = new MutationObserver(() =>
      surfaceRef.current?.setTheme(terminalTheme())
    )
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme"],
    })

    return () => {
      disposed = true
      observer.disconnect()
      if (surfaceRef.current === surface) surfaceRef.current = null
      surface?.dispose()
    }
  }, [cwd, target.kind, targetId, terminalId])

  useEffect(() => {
    const surface = surfaceRef.current
    if (!surface || state.version === previousRef.current.version) return
    const previous = previousRef.current.buffer
    if (state.buffer.startsWith(previous)) {
      surface.write(state.buffer.slice(previous.length))
    } else {
      surface.resetAndWrite(state.buffer)
    }
    previousRef.current = { buffer: state.buffer, version: state.version }
  }, [state.buffer, state.version])

  useEffect(() => {
    if (!active) return
    const frame = requestAnimationFrame(() => surfaceRef.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [active, focusRequest])

  return (
    <div
      className="relative h-full min-h-0 min-w-0 bg-panel"
      onMouseDown={onFocus}
    >
      <div ref={mountRef} className="h-full w-full overflow-hidden" />
      {selection && (
        <Inline
          className={cn(
            "absolute right-2 bottom-2 z-10 p-0.5",
            POPUP_SURFACE_SHELL
          )}
        >
          {onAddToChat && (
            <Button
              size="compact"
              variant="ghost"
              onClick={() => {
                onAddToChat(selection)
                surfaceRef.current?.clearSelection()
              }}
            >
              <Icon icon={Plus} size="sm" />
              Add to chat
            </Button>
          )}
          <PanelIconButton
            label="Copy selection"
            onClick={() => {
              void navigator.clipboard.writeText(selection)
              surfaceRef.current?.clearSelection()
            }}
          >
            <Icon icon={Copy} size="sm" />
          </PanelIconButton>
        </Inline>
      )}
      {target.kind === "cloud" && state.status === "starting" && (
        <Badge className="absolute top-2 left-2 shadow-popup">
          <Spinner size="sm" />
          {state.buffer ? "Reconnecting…" : "Connecting…"}
        </Badge>
      )}
      {(error || state.error) && (
        <Alert className="absolute inset-x-2 top-2 w-auto" tone="risk">
          <AlertDescription>{error ?? state.error}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}

export function TerminalActions({
  groupId,
  terminals,
}: {
  groupId: string
  terminals: TerminalGroupsController
}) {
  const terminalIds =
    terminals.state.terminalGroups.find((group) => group.id === groupId)
      ?.terminalIds ?? []
  const activeTerminalId = terminalIds.includes(
    terminals.state.activeTerminalId
  )
    ? terminals.state.activeTerminalId
    : (terminalIds[0] ?? "")
  const atSplitLimit = terminalIds.length >= MAX_TERMINALS_PER_GROUP

  // Splits follow the focused group, which can differ from the visible tab.
  const split = (direction: TerminalSplitDirection) => {
    if (activeTerminalId) terminals.focus(activeTerminalId)
    terminals.split(direction)
  }

  return (
    <Inline className="shrink-0">
      <PanelIconButton
        label={`Split horizontally${atSplitLimit ? " (maximum 4)" : ""}`}
        disabled={atSplitLimit}
        onClick={() => split("horizontal")}
      >
        <Icon icon={Columns} size="sm" />
      </PanelIconButton>
      <PanelIconButton
        label={`Split vertically${atSplitLimit ? " (maximum 4)" : ""}`}
        disabled={atSplitLimit}
        onClick={() => split("vertical")}
      >
        <Icon icon={Rows} size="sm" />
      </PanelIconButton>
      <PanelIconButton
        label="Clear terminal"
        onClick={() => terminals.clear(activeTerminalId)}
      >
        <Icon icon={Trash2} size="sm" />
      </PanelIconButton>
      <PanelIconButton
        label="Restart terminal"
        onClick={() => terminals.restart(activeTerminalId)}
      >
        <Icon icon={RefreshCw} size="sm" />
      </PanelIconButton>
      {terminalIds.length > 1 ? (
        <PanelIconButton
          label="Close terminal"
          onClick={() => terminals.closeTerminal(activeTerminalId)}
        >
          <Icon icon={X} size="sm" />
        </PanelIconButton>
      ) : null}
    </Inline>
  )
}

export function TerminalPanel({
  target,
  cwd,
  groupId,
  terminals,
  onOpenFile,
  onAddToChat,
}: TerminalPanelProps) {
  const [focusRequest, setFocusRequest] = useState(0)
  const group = terminals.state.terminalGroups.find(
    (candidate) => candidate.id === groupId
  )
  const terminalIds = group?.terminalIds ?? []
  const activeTerminalId = terminalIds.includes(
    terminals.state.activeTerminalId
  )
    ? terminals.state.activeTerminalId
    : (terminalIds[0] ?? "")

  return (
    <div
      className="relative flex h-full min-h-0 flex-col"
      data-hotkeys="ignore"
    >
      {terminals.error && (
        <Alert className="absolute inset-x-2 top-2 z-10 w-auto" tone="risk">
          <AlertDescription>{terminals.error}</AlertDescription>
        </Alert>
      )}

      <div
        className="grid min-h-0 flex-1"
        style={
          group?.splitDirection === "vertical"
            ? {
                gridTemplateRows: `repeat(${terminalIds.length}, minmax(0, 1fr))`,
              }
            : {
                gridTemplateColumns: `repeat(${terminalIds.length}, minmax(0, 1fr))`,
              }
        }
      >
        {terminalIds.map((terminalId, index) => (
          <div
            key={terminalId}
            className={cn(
              "min-h-0 min-w-0",
              index > 0 &&
                (group?.splitDirection === "vertical"
                  ? "border-t border-line"
                  : "border-l border-line")
            )}
          >
            <TerminalViewport
              target={target}
              terminalId={terminalId}
              cwd={terminals.metadataById.get(terminalId)?.cwd ?? cwd}
              active={activeTerminalId === terminalId}
              focusRequest={focusRequest}
              onFocus={() => {
                terminals.focus(terminalId)
                setFocusRequest((value) => value + 1)
              }}
              onOpenFile={onOpenFile}
              onAddToChat={onAddToChat}
              clearRequest={terminals.clearRequests.get(terminalId) ?? 0}
              restartRequest={terminals.restartRequests.get(terminalId) ?? 0}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
