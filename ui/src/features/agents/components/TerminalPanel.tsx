import { useEffect, useRef, useState } from "react"
import {
  Copy,
  LoaderCircle,
  Plus,
  RefreshCw,
  SquareSplitHorizontal,
  SquareSplitVertical,
  Trash2,
  X,
} from "lucide-react"

import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import { MAX_TERMINALS_PER_GROUP } from "@/features/agents/lib/terminalState"
import { cn } from "@/lib/utils"
import { useAttachedTerminal } from "@/features/agents/lib/terminalSession"
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

function terminalTheme() {
  const dark = document.documentElement.classList.contains("dark")
  return dark
    ? {
        background: { r: 28, g: 28, b: 28 },
        foreground: { r: 229, g: 231, b: 235 },
        cursor: { r: 229, g: 231, b: 235 },
        selectionBackground: "rgba(147, 197, 253, 0.25)",
      }
    : {
        background: { r: 255, g: 255, b: 255 },
        foreground: { r: 31, g: 41, b: 55 },
        cursor: { r: 31, g: 41, b: 55 },
        selectionBackground: "rgba(37, 99, 235, 0.2)",
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
        if (target.kind !== "local" || !onOpenFile) return
        const path = text.replace(/:\d+(?::\d+)?$/, "")
        void window.openSweDesktop
          ?.resolveLocalProjectPath({ threadId: target.sessionId, path })
          .then((relativePath) => {
            if (relativePath) onOpenFile(relativePath)
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
        if (active) created.focus()
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
      className="relative h-full min-h-0 min-w-0 bg-[#1c1c1c] dark:bg-[#1c1c1c]"
      onMouseDown={onFocus}
    >
      <div ref={mountRef} className="h-full w-full overflow-hidden" />
      {selection && (
        <div className="absolute right-2 bottom-2 z-10 flex overflow-hidden rounded-md border border-border bg-background shadow-sm">
          {onAddToChat && (
            <button
              type="button"
              className="flex items-center gap-1 px-2 py-1 text-[11px] hover:bg-accent"
              onClick={() => {
                onAddToChat(selection)
                surfaceRef.current?.clearSelection()
              }}
            >
              <Plus className="size-3" /> Add to chat
            </button>
          )}
          <button
            type="button"
            aria-label="Copy selection"
            className={cn(
              "p-1.5 hover:bg-accent",
              onAddToChat && "border-l border-border"
            )}
            onClick={() => {
              void navigator.clipboard.writeText(selection)
              surfaceRef.current?.clearSelection()
            }}
          >
            <Copy className="size-3" />
          </button>
        </div>
      )}
      {target.kind === "cloud" && state.status === "starting" && (
        <div className="absolute top-2 left-2 flex items-center gap-1.5 rounded-md border border-border bg-background/95 px-2 py-1 text-xs text-muted-foreground shadow-sm">
          <LoaderCircle className="size-3 animate-spin" />
          {state.buffer ? "Reconnecting…" : "Connecting…"}
        </div>
      )}
      {(error || state.error) && (
        <div className="absolute inset-x-2 top-2 rounded-md border border-destructive/40 bg-background/95 px-3 py-2 text-xs text-destructive shadow-sm">
          {error ?? state.error}
        </div>
      )}
    </div>
  )
}

function ActionButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
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
  const atSplitLimit = terminalIds.length >= MAX_TERMINALS_PER_GROUP

  return (
    <div
      className="group/terminal relative flex h-full min-h-0 flex-col"
      data-hotkeys="ignore"
    >
      <div className="absolute top-1 right-2 z-10 flex items-center rounded-md border border-border bg-background/95 opacity-0 shadow-sm transition-opacity group-hover/terminal:opacity-100 focus-within:opacity-100">
        <ActionButton
          label={`Split horizontally${atSplitLimit ? " (maximum 4)" : ""}`}
          disabled={atSplitLimit}
          onClick={() => terminals.split("horizontal")}
        >
          <SquareSplitHorizontal className="size-3.5" />
        </ActionButton>
        <ActionButton
          label={`Split vertically${atSplitLimit ? " (maximum 4)" : ""}`}
          disabled={atSplitLimit}
          onClick={() => terminals.split("vertical")}
        >
          <SquareSplitVertical className="size-3.5" />
        </ActionButton>
        <ActionButton
          label="Clear terminal"
          onClick={() => terminals.clear(activeTerminalId)}
        >
          <Trash2 className="size-3.5" />
        </ActionButton>
        <ActionButton
          label="Restart terminal"
          onClick={() => terminals.restart(activeTerminalId)}
        >
          <RefreshCw className="size-3.5" />
        </ActionButton>
        {terminalIds.length > 1 && (
          <ActionButton
            label="Close terminal"
            onClick={() => terminals.closeTerminal(activeTerminalId)}
          >
            <X className="size-3.5" />
          </ActionButton>
        )}
      </div>

      {terminals.error && (
        <div className="absolute inset-x-2 top-2 z-10 rounded-md border border-destructive/40 bg-background/95 px-3 py-2 text-xs text-destructive shadow-sm">
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
                  ? "border-t border-border"
                  : "border-l border-border")
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
