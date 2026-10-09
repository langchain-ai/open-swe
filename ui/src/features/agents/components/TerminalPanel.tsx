import { Button } from "@langchain/macaw-components/Button"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { PlusIcon } from "@phosphor-icons/react/dist/ssr/Plus"
import { SquareSplitHorizontalIcon } from "@phosphor-icons/react/dist/ssr/SquareSplitHorizontal"
import { SquareSplitVerticalIcon } from "@phosphor-icons/react/dist/ssr/SquareSplitVertical"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useEffect, useRef, useState } from "react"

import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import type { TerminalSplitDirection } from "@/features/agents/lib/terminalState"
import { MAX_TERMINALS_PER_GROUP } from "@/features/agents/lib/terminalState"
import { cn } from "@/lib/utils"
import { useAttachedTerminal } from "@/features/agents/lib/terminalSession"
import type {
  GhosttyColor,
  GhosttyTheme,
} from "@/features/agents/terminal/ghostty/core"
import { GhosttyTerminalSurface } from "@/features/agents/terminal/ghostty/surface"

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

const ANSI_TOKENS = [
  "syntax-comment",
  "text-error-secondary",
  "syntax-string",
  "syntax-constant",
  "syntax-keyword",
  "syntax-function",
  "syntax-type",
  "syntax-plain",
]

function terminalTheme(): GhosttyTheme {
  const styles = getComputedStyle(document.documentElement)
  const context = document.createElement("canvas").getContext("2d")
  const token = (name: string): GhosttyColor => {
    if (context) context.fillStyle = styles.getPropertyValue(`--${name}`)
    const hex = Number.parseInt(String(context?.fillStyle).slice(1), 16) || 0
    return { r: hex >> 16, g: (hex >> 8) & 255, b: hex & 255 }
  }
  const ansi = ANSI_TOKENS.map(token)
  return {
    background: token("bg-surface-level-1"),
    foreground: token("text-primary"),
    cursor: token("icon-brand"),
    selectionBackground: styles.getPropertyValue("--bg-brand-subtle"),
    ansi: [
      ...ansi,
      token("syntax-operator"),
      ...ansi.slice(1, 7),
      token("text-primary"),
    ],
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
          "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
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
      attributeFilter: ["class", "style"],
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
      className="relative h-full min-h-0 min-w-0 bg-surface-level-1"
      onMouseDown={onFocus}
    >
      <div ref={mountRef} className="h-full w-full overflow-hidden" />
      {selection && (
        <div className="absolute right-2 bottom-2 z-10 flex items-center gap-0.5 rounded-md border border-default bg-elevated p-0.5 shadow-sm">
          {onAddToChat && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              leftDecorator={PlusIcon}
              onClick={() => {
                onAddToChat(selection)
                surfaceRef.current?.clearSelection()
              }}
            >
              Add to chat
            </Button>
          )}
          <IconButton
            icon={CopyIcon}
            label="Copy selection"
            size="xs"
            color="secondary"
            variant="plain"
            onClick={() => {
              void navigator.clipboard.writeText(selection)
              surfaceRef.current?.clearSelection()
            }}
          />
        </div>
      )}
      {target.kind === "cloud" && state.status === "starting" && (
        <div className="absolute top-2 left-2 flex items-center gap-1.5 rounded-md border border-default bg-surface-level-1/95 px-2 py-1 text-xs text-secondary shadow-sm">
          <Spinner size="xxs" />
          {state.buffer ? "Reconnecting…" : "Connecting…"}
        </div>
      )}
      {(error || state.error) && (
        <div className="absolute inset-x-2 top-2 rounded-md border border-error bg-surface-level-1/95 px-3 py-2 text-xs text-error-secondary shadow-sm">
          {error ?? state.error}
        </div>
      )}
    </div>
  )
}

function ActionButton({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string
  icon: IconComponent
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <IconButton
      icon={icon}
      label={label}
      size="sm"
      color="secondary"
      variant="plain"
      disabled={disabled}
      onClick={onClick}
    />
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
    <div className="flex shrink-0 items-center">
      <ActionButton
        label={`Split horizontally${atSplitLimit ? " (maximum 4)" : ""}`}
        disabled={atSplitLimit}
        icon={SquareSplitHorizontalIcon}
        onClick={() => split("horizontal")}
      />
      <ActionButton
        label={`Split vertically${atSplitLimit ? " (maximum 4)" : ""}`}
        disabled={atSplitLimit}
        icon={SquareSplitVerticalIcon}
        onClick={() => split("vertical")}
      />
      <ActionButton
        label="Clear terminal"
        icon={TrashIcon}
        onClick={() => terminals.clear(activeTerminalId)}
      />
      <ActionButton
        label="Restart terminal"
        icon={ArrowClockwiseIcon}
        onClick={() => terminals.restart(activeTerminalId)}
      />
      {terminalIds.length > 1 ? (
        <ActionButton
          label="Close terminal"
          icon={XIcon}
          onClick={() => terminals.closeTerminal(activeTerminalId)}
        />
      ) : null}
    </div>
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
        <div className="absolute inset-x-2 top-2 z-10 rounded-md border border-error bg-surface-level-1/95 px-3 py-2 text-xs text-error-secondary shadow-sm">
          {terminals.error}
        </div>
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
                  ? "border-t border-default"
                  : "border-l border-default")
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
