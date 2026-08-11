import { useEffect, useMemo, useState } from "react"

import type {
  DesktopTerminalAttachEvent,
  DesktopTerminalSessionSnapshot,
  DesktopTerminalSummary,
} from "@/desktop"

export interface TerminalSessionState {
  buffer: string
  status: DesktopTerminalSessionSnapshot["status"] | "closed"
  error: string | null
  summary: DesktopTerminalSummary | null
  version: number
  sequence: number
}

export const EMPTY_TERMINAL_SESSION: TerminalSessionState = Object.freeze({
  buffer: "",
  status: "closed",
  error: null,
  summary: null,
  version: 0,
  sequence: -1,
})

const MAX_BUFFER_BYTES = 512 * 1024
const encoder = new TextEncoder()
const decoder = new TextDecoder()

function trimBuffer(buffer: string): string {
  const bytes = encoder.encode(buffer)
  if (bytes.byteLength <= MAX_BUFFER_BYTES) return buffer
  let start = bytes.byteLength - MAX_BUFFER_BYTES
  while (start < bytes.length && (bytes[start]! & 0xc0) === 0x80) start += 1
  return decoder.decode(bytes.subarray(start))
}

function summaryFromSnapshot(
  snapshot: DesktopTerminalSessionSnapshot
): DesktopTerminalSummary {
  const { history: _history, sequence: _sequence, ...summary } = snapshot
  return summary
}

export function applyTerminalSnapshot(
  current: TerminalSessionState,
  snapshot: DesktopTerminalSessionSnapshot
): TerminalSessionState {
  if (snapshot.sequence <= current.sequence) return current
  return {
    buffer: trimBuffer(snapshot.history),
    status: snapshot.status,
    error: null,
    summary: summaryFromSnapshot(snapshot),
    version: current.version + 1,
    sequence: snapshot.sequence,
  }
}

export function applyTerminalEvent(
  current: TerminalSessionState,
  event: DesktopTerminalAttachEvent
): TerminalSessionState {
  if (event.sequence <= current.sequence) return current

  switch (event.type) {
    case "started":
    case "restarted":
      return applyTerminalSnapshot(current, {
        ...event.snapshot,
        sequence: event.sequence,
      })
    case "output":
      return {
        ...current,
        buffer: trimBuffer(`${current.buffer}${event.data}`),
        status: current.status === "closed" ? "running" : current.status,
        error: null,
        version: current.version + 1,
        sequence: event.sequence,
      }
    case "cleared":
      return {
        ...current,
        buffer: "",
        error: null,
        version: current.version + 1,
        sequence: event.sequence,
      }
    case "exited":
      return {
        ...current,
        status: "exited",
        error: null,
        summary: current.summary
          ? {
              ...current.summary,
              status: "exited",
              pid: null,
              exitCode: event.exitCode,
              exitSignal: event.exitSignal,
              hasRunningSubprocess: false,
            }
          : null,
        version: current.version + 1,
        sequence: event.sequence,
      }
    case "closed":
      return {
        ...current,
        status: "closed",
        error: null,
        summary: null,
        version: current.version + 1,
        sequence: event.sequence,
      }
    case "error":
      return {
        ...current,
        status: "error",
        error: event.message,
        summary: current.summary
          ? { ...current.summary, status: "error" }
          : null,
        version: current.version + 1,
        sequence: event.sequence,
      }
    case "activity":
      return {
        ...current,
        summary: current.summary
          ? {
              ...current.summary,
              hasRunningSubprocess: event.hasRunningSubprocess,
              label: event.label,
            }
          : null,
        version: current.version + 1,
        sequence: event.sequence,
      }
  }
}

export function useDesktopTerminalMetadata(localSessionId: string) {
  const [terminals, setTerminals] = useState<Array<DesktopTerminalSummary>>([])

  useEffect(() => {
    const bridge = window.openSweDesktop?.terminal
    if (!bridge) return
    let disposed = false
    const pending: Array<
      | { type: "upsert"; terminal: DesktopTerminalSummary }
      | { type: "remove"; terminalId: string }
    > = []
    let subscribed = false
    const apply = (
      event:
        | { type: "upsert"; terminal: DesktopTerminalSummary }
        | { type: "remove"; terminalId: string }
    ) => {
      setTerminals((current) =>
        event.type === "remove"
          ? current.filter(
              (terminal) => terminal.terminalId !== event.terminalId
            )
          : [
              ...current.filter(
                (terminal) => terminal.terminalId !== event.terminal.terminalId
              ),
              event.terminal,
            ]
      )
    }
    const remove = bridge.onMetadata((event) => {
      if (event.type === "remove") {
        if (event.localSessionId !== localSessionId) return
        const update = {
          type: "remove" as const,
          terminalId: event.terminalId,
        }
        if (subscribed) apply(update)
        else pending.push(update)
      } else if (event.terminal.localSessionId === localSessionId) {
        if (subscribed) apply(event)
        else pending.push(event)
      }
    })
    void bridge
      .subscribeMetadata(localSessionId)
      .then((next) => {
        if (disposed) return
        setTerminals(next)
        subscribed = true
        for (const update of pending.splice(0)) apply(update)
      })
      .catch(() => {})
    return () => {
      disposed = true
      remove()
      void bridge.detachMetadata(localSessionId)
    }
  }, [localSessionId])

  return useMemo(
    () =>
      [...terminals].sort((left, right) =>
        left.terminalId.localeCompare(right.terminalId, undefined, {
          numeric: true,
        })
      ),
    [terminals]
  )
}

export function useAttachedTerminal(
  localSessionId: string,
  terminalId: string,
  cwd: string
): TerminalSessionState {
  const [state, setState] = useState<TerminalSessionState>(
    EMPTY_TERMINAL_SESSION
  )

  useEffect(() => {
    const bridge = window.openSweDesktop?.terminal
    if (!bridge) {
      setState({
        ...EMPTY_TERMINAL_SESSION,
        status: "error",
        error: "Local terminal is only available in the desktop app.",
      })
      return
    }
    let disposed = false
    setState(EMPTY_TERMINAL_SESSION)
    const remove = bridge.onEvent((event) => {
      if (
        event.localSessionId === localSessionId &&
        event.terminalId === terminalId &&
        !disposed
      ) {
        setState((current) => applyTerminalEvent(current, event))
      }
    })
    void bridge
      .attach({
        localSessionId,
        terminalId,
        cwd,
      })
      .then((snapshot) => {
        if (!disposed) {
          setState((current) => applyTerminalSnapshot(current, snapshot))
        }
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setState((current) => ({
            ...current,
            status: "error",
            error:
              error instanceof Error
                ? error.message
                : "Unable to attach terminal",
            version: current.version + 1,
          }))
        }
      })

    return () => {
      disposed = true
      remove()
      void bridge.detach({ localSessionId, terminalId })
    }
  }, [cwd, localSessionId, terminalId])

  return state
}
