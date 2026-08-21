import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type {
  DesktopTerminalAttachEvent,
  DesktopTerminalSessionSnapshot,
  DesktopTerminalSummary,
} from "@/desktop"
import { agentsApi } from "@/features/agents/lib/api"
import type { CloudTerminalConnection } from "@/features/agents/lib/api"

export type TerminalTarget =
  { kind: "local"; sessionId: string } | { kind: "cloud"; threadId: string }

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

export function useDesktopTerminalMetadata(
  localSessionId: string,
  enabled = true
) {
  const [terminals, setTerminals] = useState<Array<DesktopTerminalSummary>>([])

  useEffect(() => {
    const bridge = window.openSweDesktop?.terminal
    if (!enabled || !bridge) return
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
  }, [enabled, localSessionId])

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

export interface AttachedTerminal extends TerminalSessionState {
  write: (data: string) => Promise<void>
  resize: (cols: number, rows: number) => void
}

export function cloudTerminalProtocols(
  connection: CloudTerminalConnection
): string[] {
  return [connection.protocol, connection.ticket]
}

export function cloudTerminalCloseError(
  reason: string,
  currentError: string | null
): string {
  return reason || currentError || "Cloud terminal disconnected"
}

export function useAttachedTerminal(
  target: TerminalTarget,
  terminalId: string,
  cwd: string,
  clearRequest = 0,
  restartRequest = 0
): AttachedTerminal {
  const [state, setState] = useState<TerminalSessionState>(
    EMPTY_TERMINAL_SESSION
  )
  const socketRef = useRef<WebSocket | null>(null)
  const cloudConnectingRef = useRef(false)
  const pendingRef = useRef<Array<object>>([])
  const sessionId = target.kind === "local" ? target.sessionId : target.threadId

  useEffect(() => {
    if (target.kind === "cloud") {
      let disposed = false
      let sequence = 0
      let socket: WebSocket | null = null
      let retryTimer: ReturnType<typeof setTimeout> | null = null
      let retryCount = 0
      let exited = false
      pendingRef.current = []
      cloudConnectingRef.current = true
      setState({ ...EMPTY_TERMINAL_SESSION, status: "starting" })

      const reconnect = () => {
        if (disposed || exited) return
        cloudConnectingRef.current = true
        setState((current) => ({
          ...current,
          status: "starting",
          error: null,
          version: current.version + 1,
        }))
        retryTimer = setTimeout(
          connect,
          Math.min(500 * 2 ** retryCount++, 10_000)
        )
      }

      const connect = () => {
        if (disposed || exited) return
        void agentsApi
          .connectCloudTerminal(target.threadId)
          .then((connection) => {
            if (disposed || exited) return
            const createdSocket = new WebSocket(
              connection.url,
              cloudTerminalProtocols(connection)
            )
            socket = createdSocket
            socketRef.current = createdSocket
            createdSocket.onopen = () => {
              if (socketRef.current !== createdSocket || disposed) return
              retryCount = 0
              cloudConnectingRef.current = false
              setState((current) => ({
                ...current,
                status: "running",
                error: null,
              }))
              for (const message of pendingRef.current.splice(0)) {
                createdSocket.send(JSON.stringify(message))
              }
            }
            createdSocket.onmessage = (event) => {
              if (
                disposed ||
                socketRef.current !== createdSocket ||
                typeof event.data !== "string"
              )
                return
              let message: {
                type: string
                data?: string
                exitCode?: number
                message?: string
              }
              try {
                message = JSON.parse(event.data)
              } catch {
                return
              }
              if (
                message.type === "output" &&
                typeof message.data === "string"
              ) {
                setState((current) => ({
                  ...current,
                  buffer: trimBuffer(`${current.buffer}${message.data}`),
                  status: "running",
                  error: null,
                  version: current.version + 1,
                  sequence: ++sequence,
                }))
              } else if (message.type === "exit") {
                exited = true
                cloudConnectingRef.current = false
                setState((current) => ({
                  ...current,
                  status: "exited",
                  version: current.version + 1,
                  sequence: ++sequence,
                }))
              } else if (message.type === "error") {
                setState((current) => ({
                  ...current,
                  error: message.message ?? "Cloud terminal disconnected",
                  version: current.version + 1,
                }))
              }
            }
            createdSocket.onclose = () => {
              if (socketRef.current !== createdSocket) return
              socketRef.current = null
              reconnect()
            }
          })
          .catch(reconnect)
      }

      connect()
      return () => {
        disposed = true
        cloudConnectingRef.current = false
        pendingRef.current = []
        if (retryTimer) clearTimeout(retryTimer)
        socket?.close()
        if (socketRef.current === socket) socketRef.current = null
      }
    }

    const bridge = window.openSweDesktop?.terminal
    if (!bridge) return
    let disposed = false
    setState(EMPTY_TERMINAL_SESSION)
    const remove = bridge.onEvent((event) => {
      if (
        event.localSessionId === target.sessionId &&
        event.terminalId === terminalId &&
        !disposed
      ) {
        setState((current) => applyTerminalEvent(current, event))
      }
    })
    void bridge
      .attach({ localSessionId: target.sessionId, terminalId, cwd })
      .then((snapshot) => {
        if (!disposed)
          setState((current) => applyTerminalSnapshot(current, snapshot))
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
      void bridge.detach({ localSessionId: target.sessionId, terminalId })
    }
  }, [cwd, restartRequest, sessionId, target.kind, terminalId])

  useEffect(() => {
    if (!clearRequest || target.kind === "local") return
    setState((current) => ({
      ...current,
      buffer: "",
      version: current.version + 1,
    }))
  }, [clearRequest, target.kind])

  const send = useCallback((message: object): boolean => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message))
      return true
    }
    if (
      socket?.readyState === WebSocket.CONNECTING ||
      cloudConnectingRef.current
    ) {
      if (pendingRef.current.length >= 100) return false
      pendingRef.current.push(message)
      return true
    }
    return false
  }, [])

  return {
    ...state,
    write: (data) =>
      target.kind === "local"
        ? (window.openSweDesktop?.terminal.write({
            localSessionId: target.sessionId,
            terminalId,
            data,
          }) ?? Promise.reject(new Error("Local terminal unavailable")))
        : send({ type: "input", data })
          ? Promise.resolve()
          : Promise.reject(new Error("Cloud terminal is disconnected")),
    resize: (cols, rows) => {
      if (target.kind === "local") {
        void window.openSweDesktop?.terminal.resize({
          localSessionId: target.sessionId,
          terminalId,
          cols,
          rows,
        })
      } else {
        pendingRef.current = pendingRef.current.filter(
          (message) => (message as { type?: string }).type !== "resize"
        )
        send({ type: "resize", cols, rows })
      }
    },
  }
}
