import { useLayoutEffect, useRef, useState } from "react"
import { FitAddon } from "@xterm/addon-fit"
import { Terminal } from "@xterm/xterm"

type TerminalPanelProps = { id: string; cwd: string }

export function TerminalPanel(props: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  // Depend on the individual fields, never the props object: it is a fresh
  // literal on every parent render, which would tear down and respawn the shell
  // each time the transcript streams a token.
  const localId = props.id
  const localCwd = props.cwd

  useLayoutEffect(() => {
    setError(null)
    const container = containerRef.current
    if (!container) return

    const terminal = new Terminal({
      cursorBlink: true,
      fontFamily:
        "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      fontSize: 13,
      theme: {
        background: "#111111",
        foreground: "#e5e7eb",
        cursor: "#e5e7eb",
      },
    })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(container)

    let disposed = false
    let resizeFrame: number | null = null
    let cleanupTransport = () => {}
    let lastCols = terminal.cols
    let lastRows = terminal.rows

    const resize = () => {
      if (
        disposed ||
        container.clientWidth === 0 ||
        container.clientHeight === 0
      )
        return
      fit.fit()
      if (
        localId &&
        (terminal.cols !== lastCols || terminal.rows !== lastRows)
      ) {
        lastCols = terminal.cols
        lastRows = terminal.rows
        window.openSweDesktop?.terminal.resize(
          localId,
          terminal.cols,
          terminal.rows
        )
      }
    }
    const scheduleResize = () => {
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null
        resize()
      })
    }
    const observer = new ResizeObserver(scheduleResize)
    observer.observe(container)

    if (localId && localCwd) {
      const bridge = window.openSweDesktop?.terminal
      if (!bridge) {
        setError("Local terminal is only available in the desktop app.")
      } else {
        const removeData = bridge.onData((id, data) => {
          if (!disposed && id === localId) terminal.write(data)
        })
        const removeError = bridge.onError((id, message) => {
          if (id === localId) setError(message)
        })
        const input = terminal.onData((data) => bridge.write(localId, data))
        bridge.create(localId, localCwd)
        cleanupTransport = () => {
          input.dispose()
          removeData()
          removeError()
          bridge.destroy(localId)
        }
      }
    }

    scheduleResize()
    void document.fonts.ready.then(scheduleResize)
    terminal.focus()

    return () => {
      disposed = true
      if (resizeFrame !== null) cancelAnimationFrame(resizeFrame)
      observer.disconnect()
      cleanupTransport()
      terminal.dispose()
    }
  }, [localCwd, localId])

  return (
    <div className="relative h-full min-h-0 bg-[#111111] p-2">
      <div ref={containerRef} className="h-full w-full" />
      {error && (
        <div className="absolute inset-x-3 top-3 rounded-md border border-destructive/40 bg-background/95 px-3 py-2 text-xs text-destructive shadow-sm">
          {error}
        </div>
      )}
    </div>
  )
}
