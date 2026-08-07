import { useLayoutEffect, useRef, useState } from "react"
import { FitAddon } from "@xterm/addon-fit"
import { Terminal } from "@xterm/xterm"

import type { ThreadTerminalEvent } from "@/features/agents/lib/api"
import { agentsApi } from "@/features/agents/lib/api"

type TerminalPanelProps =
  | { transport: "local"; id: string; cwd: string }
  | { transport: "cloud"; threadId: string }

export function TerminalPanel(props: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

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
        props.transport === "local" &&
        (terminal.cols !== lastCols || terminal.rows !== lastRows)
      ) {
        lastCols = terminal.cols
        lastRows = terminal.rows
        window.openSweDesktop?.terminal.resize(
          props.id,
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

    if (props.transport === "local") {
      const bridge = window.openSweDesktop?.terminal
      if (!bridge) {
        setError("Local terminal is only available in the desktop app.")
      } else {
        const removeData = bridge.onData((id, data) => {
          if (!disposed && id === props.id) terminal.write(data)
        })
        const removeError = bridge.onError((id, message) => {
          if (id === props.id) setError(message)
        })
        const input = terminal.onData((data) => bridge.write(props.id, data))
        bridge.create(props.id, props.cwd)
        cleanupTransport = () => {
          input.dispose()
          removeData()
          removeError()
          bridge.destroy(props.id)
        }
      }
    } else {
      let remoteId: string | null = null
      const abort = new AbortController()
      const input = terminal.onData((data) => {
        if (remoteId)
          void agentsApi.writeTerminal(props.threadId, remoteId, data)
      })
      void (async () => {
        try {
          const remote = await agentsApi.createTerminal(props.threadId)
          remoteId = remote.id
          const response = await fetch(
            agentsApi.terminalStreamUrl(props.threadId, remote.id),
            {
              credentials: "include",
              signal: abort.signal,
            }
          )
          if (!response.ok || !response.body)
            throw new Error("Terminal stream unavailable")
          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ""
          for (
            let result = await reader.read();
            !result.done;
            result = await reader.read()
          ) {
            buffer += decoder.decode(result.value, { stream: true })
            const events = buffer.split("\n\n")
            buffer = events.pop() ?? ""
            for (const raw of events) {
              const line = raw
                .split("\n")
                .find((entry) => entry.startsWith("data: "))
              if (!line) continue
              const event = JSON.parse(line.slice(6)) as ThreadTerminalEvent
              if (event.type === "output" && event.data)
                terminal.write(event.data)
              else if (event.type === "error")
                setError(event.detail ?? "Terminal connection lost")
            }
          }
        } catch (caught) {
          if (!abort.signal.aborted) {
            setError(
              caught instanceof Error ? caught.message : "Terminal unavailable"
            )
          }
        }
      })()
      cleanupTransport = () => {
        input.dispose()
        abort.abort()
        if (remoteId) void agentsApi.closeTerminal(props.threadId, remoteId)
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
  }, [props])

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
