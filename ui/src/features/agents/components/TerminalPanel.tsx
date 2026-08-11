import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Copy,
  Plus,
  RefreshCw,
  SquareSplitHorizontal,
  SquareSplitVertical,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react"

import type { TerminalUiState } from "@/features/agents/lib/terminalState"
import { cn } from "@/lib/utils"
import {
  MAX_TERMINALS_PER_GROUP,
  addTerminalGroup,
  closeTerminal,
  focusTerminal,
  readTerminalState,
  reconcileTerminalIds,
  splitTerminal,
  writeTerminalState,
} from "@/features/agents/lib/terminalState"
import {
  useAttachedTerminal,
  useDesktopTerminalMetadata,
} from "@/features/agents/lib/terminalSession"
import { GhosttyTerminalSurface } from "@/features/agents/terminal/ghostty/surface"

interface TerminalPanelProps {
  localSessionId: string
  cwd: string
  onOpenFile: (path: string) => void
  onAddToChat: (text: string) => void
}

interface TerminalViewportProps {
  localSessionId: string
  terminalId: string
  cwd: string
  active: boolean
  focusRequest: number
  onFocus: () => void
  onOpenFile: (path: string) => void
  onAddToChat: (text: string) => void
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
  localSessionId,
  terminalId,
  cwd,
  active,
  focusRequest,
  onFocus,
  onOpenFile,
  onAddToChat,
}: TerminalViewportProps) {
  const mountRef = useRef<HTMLDivElement>(null)
  const surfaceRef = useRef<GhosttyTerminalSurface | null>(null)
  const previousRef = useRef({ buffer: "", version: 0 })
  const [error, setError] = useState<string | null>(null)
  const [selection, setSelection] = useState<string | null>(null)
  const state = useAttachedTerminal(localSessionId, terminalId, cwd)
  const latestStateRef = useRef(state)
  latestStateRef.current = state

  useEffect(() => {
    const mount = mountRef.current
    const bridge = window.openSweDesktop?.terminal
    if (!mount || !bridge) return
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
        void bridge
          .write({ localSessionId, terminalId, data })
          .catch((cause) =>
            setError(
              cause instanceof Error ? cause.message : "Terminal write failed"
            )
          )
      },
      onResize: (cols, rows) => {
        void bridge.resize({ localSessionId, terminalId, cols, rows })
      },
      onSelectionChange: () => {
        const text = surfaceRef.current?.getSelection().trim() ?? ""
        setSelection(text || null)
      },
      onCopy: (text) => void navigator.clipboard.writeText(text),
      beforeKey: () => true,
      onLinkActivate: (text, event) => {
        if (!(event.metaKey || event.ctrlKey)) return
        const desktop = window.openSweDesktop
        if (!desktop) return
        if (/^https?:\/\//i.test(text)) {
          void desktop.openExternal(text)
          return
        }
        const path = text.replace(/:\d+(?::\d+)?$/, "")
        void desktop
          .resolveLocalProjectPath({ localSessionId, path })
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
  }, [cwd, localSessionId, terminalId])

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
          <button
            type="button"
            aria-label="Copy selection"
            className="border-l border-border p-1.5 hover:bg-accent"
            onClick={() => {
              void navigator.clipboard.writeText(selection)
              surfaceRef.current?.clearSelection()
            }}
          >
            <Copy className="size-3" />
          </button>
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
  localSessionId,
  cwd,
  onOpenFile,
  onAddToChat,
}: TerminalPanelProps) {
  const [state, setState] = useState<TerminalUiState>(() =>
    readTerminalState(localSessionId)
  )
  const metadata = useDesktopTerminalMetadata(localSessionId)
  const [focusRequest, setFocusRequest] = useState(0)
  const [actionError, setActionError] = useState<string | null>(null)

  const updateState = useCallback(
    (update: (current: TerminalUiState) => TerminalUiState) => {
      setState((current) => {
        const next = update(current)
        writeTerminalState(localSessionId, next)
        return next
      })
    },
    [localSessionId]
  )

  useEffect(() => {
    setState(readTerminalState(localSessionId))
    setActionError(null)
  }, [localSessionId])

  useEffect(() => {
    updateState((current) =>
      reconcileTerminalIds(
        current,
        metadata.map((terminal) => terminal.terminalId)
      )
    )
  }, [metadata, updateState])

  const activeGroup = state.terminalGroups.find(
    (group) => group.id === state.activeTerminalGroupId
  )
  const visibleIds = activeGroup?.terminalIds ?? []
  const metadataById = useMemo(
    () => new Map(metadata.map((terminal) => [terminal.terminalId, terminal])),
    [metadata]
  )
  const atSplitLimit = visibleIds.length >= MAX_TERMINALS_PER_GROUP
  const showGroupHeaders =
    state.terminalGroups.length > 1 ||
    state.terminalGroups.some((group) => group.terminalIds.length > 1)

  const runStateAction = useCallback(
    async (terminalId: string, action: "clear" | "restart") => {
      const bridge = window.openSweDesktop?.terminal
      if (!bridge) return
      setActionError(null)
      try {
        if (action === "clear") {
          await bridge.clear({ localSessionId, terminalId })
          return
        }
        const surface = metadataById.get(terminalId)
        await bridge.restart({
          localSessionId,
          terminalId,
          cwd: surface?.cwd ?? cwd,
        })
      } catch (error) {
        setActionError(
          error instanceof Error
            ? error.message
            : `Unable to ${action} terminal`
        )
      }
    },
    [cwd, metadataById, localSessionId]
  )

  const close = useCallback(
    async (terminalId: string) => {
      const bridge = window.openSweDesktop?.terminal
      if (!bridge) return
      setActionError(null)
      try {
        await bridge.close({
          localSessionId,
          terminalId,
          deleteHistory: true,
        })
        updateState((current) => closeTerminal(current, terminalId))
      } catch (error) {
        setActionError(
          error instanceof Error ? error.message : "Unable to close terminal"
        )
      }
    },
    [localSessionId, updateState]
  )

  if (state.terminalIds.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <button
          type="button"
          className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-accent"
          onClick={() => updateState(addTerminalGroup)}
        >
          New terminal
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <div className="flex h-9 shrink-0 items-center border-b border-border px-2">
        <ActionButton
          label={`Split horizontally${atSplitLimit ? " (maximum 4)" : ""}`}
          disabled={atSplitLimit}
          onClick={() =>
            updateState((current) => splitTerminal(current, "horizontal"))
          }
        >
          <SquareSplitHorizontal className="size-3.5" />
        </ActionButton>
        <ActionButton
          label={`Split vertically${atSplitLimit ? " (maximum 4)" : ""}`}
          disabled={atSplitLimit}
          onClick={() =>
            updateState((current) => splitTerminal(current, "vertical"))
          }
        >
          <SquareSplitVertical className="size-3.5" />
        </ActionButton>
        <ActionButton
          label="New terminal group"
          onClick={() => updateState(addTerminalGroup)}
        >
          <Plus className="size-3.5" />
        </ActionButton>
        <div className="mx-1 h-4 w-px bg-border" />
        <ActionButton
          label="Clear terminal"
          onClick={() => void runStateAction(state.activeTerminalId, "clear")}
        >
          <Trash2 className="size-3.5" />
        </ActionButton>
        <ActionButton
          label="Restart terminal"
          onClick={() => void runStateAction(state.activeTerminalId, "restart")}
        >
          <RefreshCw className="size-3.5" />
        </ActionButton>
        <ActionButton
          label="Close terminal"
          onClick={() => void close(state.activeTerminalId)}
        >
          <X className="size-3.5" />
        </ActionButton>
        {actionError && (
          <span className="ml-2 truncate text-xs text-destructive">
            {actionError}
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <div
            className="grid h-full min-h-0"
            style={
              activeGroup?.splitDirection === "vertical"
                ? {
                    gridTemplateRows: `repeat(${visibleIds.length}, minmax(0, 1fr))`,
                  }
                : {
                    gridTemplateColumns: `repeat(${visibleIds.length}, minmax(0, 1fr))`,
                  }
            }
          >
            {visibleIds.map((terminalId, index) => (
              <div
                key={terminalId}
                className={cn(
                  "min-h-0 min-w-0 p-1",
                  index > 0 &&
                    (activeGroup?.splitDirection === "vertical"
                      ? "border-t border-border"
                      : "border-l border-border")
                )}
              >
                <TerminalViewport
                  localSessionId={localSessionId}
                  terminalId={terminalId}
                  cwd={metadataById.get(terminalId)?.cwd ?? cwd}
                  active={state.activeTerminalId === terminalId}
                  focusRequest={focusRequest}
                  onFocus={() => {
                    updateState((current) => focusTerminal(current, terminalId))
                    setFocusRequest((value) => value + 1)
                  }}
                  onOpenFile={onOpenFile}
                  onAddToChat={onAddToChat}
                />
              </div>
            ))}
          </div>
        </div>

        {state.terminalIds.length > 1 && (
          <aside className="flex w-36 min-w-36 shrink-0 flex-col overflow-y-auto border-l border-border p-1">
            {state.terminalGroups.map((group, groupIndex) => {
              const isGroupActive = group.terminalIds.includes(
                state.activeTerminalId
              )
              const groupActiveTerminalId = isGroupActive
                ? state.activeTerminalId
                : group.terminalIds[0]

              return (
                <div key={group.id} className="pb-0.5">
                  {showGroupHeaders && (
                    <button
                      type="button"
                      className={cn(
                        "flex w-full items-center rounded px-1 py-0.5 text-[10px] tracking-[0.08em] uppercase",
                        isGroupActive
                          ? "bg-accent/70 text-foreground"
                          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                      )}
                      onClick={() => {
                        if (groupActiveTerminalId) {
                          updateState((current) =>
                            focusTerminal(current, groupActiveTerminalId)
                          )
                        }
                      }}
                    >
                      Group {groupIndex + 1}
                    </button>
                  )}
                  <div
                    className={cn(
                      showGroupHeaders &&
                        "ml-1 border-l border-border/60 pl-1.5"
                    )}
                  >
                    {group.terminalIds.map((terminalId) => {
                      const summary = metadataById.get(terminalId)
                      const running = summary?.hasRunningSubprocess === true
                      const isActive = terminalId === state.activeTerminalId
                      return (
                        <div
                          key={terminalId}
                          className={cn(
                            "group flex items-center gap-1 rounded px-1 py-0.5 text-[11px]",
                            isActive
                              ? "bg-accent text-foreground"
                              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                          )}
                        >
                          {showGroupHeaders && (
                            <span className="text-[10px] text-muted-foreground/80">
                              └
                            </span>
                          )}
                          <button
                            type="button"
                            className="flex min-w-0 flex-1 items-center gap-1 text-left"
                            onClick={() => {
                              updateState((current) =>
                                focusTerminal(current, terminalId)
                              )
                              setFocusRequest((value) => value + 1)
                            }}
                          >
                            <TerminalSquare className="size-3 shrink-0" />
                            <span className="truncate">
                              {summary?.label || terminalId}
                            </span>
                            {running && (
                              <span
                                className="ml-auto size-1.5 shrink-0 rounded-full bg-emerald-500"
                                title="Running process"
                              />
                            )}
                          </button>
                          <button
                            type="button"
                            aria-label={`Close ${summary?.label || terminalId}`}
                            className="inline-flex size-3.5 items-center justify-center rounded text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:bg-accent hover:text-foreground"
                            onClick={() => void close(terminalId)}
                          >
                            <X className="size-2.5" />
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </aside>
        )}
      </div>
    </div>
  )
}
