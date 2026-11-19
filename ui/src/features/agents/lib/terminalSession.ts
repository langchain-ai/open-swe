import { useCallback, useEffect, useRef, useState } from "react"

import type { CloudTerminalConnection } from "@/features/agents/lib/api"
import { agentsApi } from "@/features/agents/lib/api"

/** The thread whose sandbox a terminal, or the file browser, works in. */
export interface TerminalTarget {
  threadId: string
}

export interface TerminalSessionState {
  buffer: string
  status: "starting" | "running" | "exited" | "error" | "closed"
  error: string | null
  version: number
  sequence: number
}

const EMPTY_TERMINAL_SESSION: TerminalSessionState = Object.freeze({
  buffer: "",
  status: "closed",
  error: null,
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

export interface AttachedTerminal extends TerminalSessionState {
  write: (data: string) => Promise<void>
  resize: (cols: number, rows: number) => void
}

export function cloudTerminalProtocols(
  connection: CloudTerminalConnection
): Array<string> {
  return [connection.protocol, connection.ticket]
}

export function cloudTerminalCloseError(
  reason: string,
  currentError: string | null
): string {
  return reason || currentError || "Cloud terminal disconnected"
}

const MAX_CLOUD_RECONNECT_ATTEMPTS = 5

export function cloudTerminalReconnectDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 10_000)
}

export function shouldReconnectCloudTerminal(
  code: number,
  attempt: number
): boolean {
  if (code === 1000 || code === 1008) return false
  return attempt < MAX_CLOUD_RECONNECT_ATTEMPTS
}

export function useAttachedTerminal(
  target: TerminalTarget,
  terminalId: string,
  clearRequest = 0,
  restartRequest = 0
): AttachedTerminal {
  const [state, setState] = useState<TerminalSessionState>(
    EMPTY_TERMINAL_SESSION
  )
  const socketRef = useRef<WebSocket | null>(null)
  const cloudConnectingRef = useRef(false)
  const pendingRef = useRef<Array<object>>([])
  const threadId = target.threadId

  useEffect(() => {
    let disposed = false
    let sequence = 0
    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let retryCount = 0
    let exited = false
    pendingRef.current = []
    cloudConnectingRef.current = true
    // oxlint-disable-next-line react/set-state-in-effect
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
        cloudTerminalReconnectDelay(retryCount++)
      )
    }

    const stop = (reason: string | null) => {
      cloudConnectingRef.current = false
      pendingRef.current = []
      setState((current) =>
        reason === null
          ? current.status === "error"
            ? current
            : { ...current, status: "closed", version: current.version + 1 }
          : {
              ...current,
              status: "error",
              error: cloudTerminalCloseError(reason, current.error),
              version: current.version + 1,
            }
      )
    }

    const connect = () => {
      if (disposed || exited) return
      void agentsApi
        .connectCloudTerminal(threadId)
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
            if (message.type === "output" && typeof message.data === "string") {
              retryCount = 0
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
          createdSocket.onclose = (event) => {
            if (socketRef.current !== createdSocket) return
            socketRef.current = null
            if (disposed || exited) return
            if (shouldReconnectCloudTerminal(event.code, retryCount)) {
              reconnect()
              return
            }
            stop(event.code === 1000 ? null : event.reason)
          }
        })
        .catch((error: unknown) => {
          if (disposed || exited) return
          if (retryCount < MAX_CLOUD_RECONNECT_ATTEMPTS) {
            reconnect()
            return
          }
          stop(
            error instanceof Error
              ? error.message
              : "Unable to connect to cloud terminal"
          )
        })
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
  }, [restartRequest, threadId, terminalId])

  useEffect(() => {
    if (!clearRequest) return
    // oxlint-disable-next-line react/set-state-in-effect
    setState((current) => ({
      ...current,
      buffer: "",
      version: current.version + 1,
    }))
  }, [clearRequest])

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
      send({ type: "input", data })
        ? Promise.resolve()
        : Promise.reject(new Error("Cloud terminal is disconnected")),
    resize: (cols, rows) => {
      pendingRef.current = pendingRef.current.filter(
        (message) => (message as { type?: string }).type !== "resize"
      )
      send({ type: "resize", cols, rows })
    },
  }
}
