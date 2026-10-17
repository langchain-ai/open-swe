import { useCallback, useEffect, useRef, useState } from "react"

import type { FrameMetadata, ViewerMessage } from "./browserInput"
import { agentsApi } from "./api"

const RECONNECT_DELAY_MS = 1000
const MAX_RECONNECT_DELAY_MS = 15_000
// The backend refuses the socket with this code for good: a closed window, or
// a person who may not watch it.
const POLICY_VIOLATION = 1008

interface ServerMessage {
  type: string
  data?: string
  metadata?: FrameMetadata
  url?: string
  message?: string
}

export interface BrowserView {
  /** The latest frame as a data URL, or null before the first. */
  frame: string | null
  /** The size of the page the latest frame shows. */
  metadata: FrameMetadata | null
  /** The tab's URL as the browser last reported it. */
  url: string | null
  connected: boolean
  /** Why the view stopped for good, when it did. */
  error: string | null
  /** Sends input to the tab; false when the view is not connected. */
  send: (message: ViewerMessage) => boolean
}

/**
 * The live view of one tab of a thread's browser window: JPEG frames from the
 * backend's relay, reconnecting with backoff until the backend refuses it.
 */
export function useBrowserView(
  threadId: string,
  tabId: string | null
): BrowserView {
  const [frame, setFrame] = useState<string | null>(null)
  const [metadata, setMetadata] = useState<FrameMetadata | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!tabId) return
    let disposed = false
    let retry: ReturnType<typeof setTimeout> | undefined
    let delay = RECONNECT_DELAY_MS

    const scheduleReconnect = () => {
      if (disposed) return
      retry = setTimeout(() => void connect(), delay)
      delay = Math.min(delay * 2, MAX_RECONNECT_DELAY_MS)
    }

    const connect = async () => {
      let connection
      try {
        connection = await agentsApi.connectThreadBrowser(threadId)
      } catch (cause) {
        if (disposed) return
        setError(
          cause instanceof Error ? cause.message : "Couldn't open the browser"
        )
        return
      }
      if (disposed) return
      const socketUrl = new URL(connection.url)
      socketUrl.searchParams.set("tab", tabId)
      const socket = new WebSocket(socketUrl, [
        connection.protocol,
        connection.ticket,
      ])
      socketRef.current = socket
      socket.onopen = () => {
        if (socketRef.current !== socket) return
        delay = RECONNECT_DELAY_MS
        setConnected(true)
      }
      socket.onmessage = (event: MessageEvent<string>) => {
        const message = JSON.parse(event.data) as ServerMessage
        if (message.type === "frame" && message.data) {
          if (message.metadata) setMetadata(message.metadata)
          setFrame(`data:image/jpeg;base64,${message.data}`)
        } else if (message.type === "url" && message.url !== undefined) {
          setUrl(message.url)
        }
      }
      socket.onclose = (event) => {
        if (socketRef.current !== socket) return
        socketRef.current = null
        setConnected(false)
        if (event.code === POLICY_VIOLATION) {
          setError(event.reason || "This browser window is unavailable")
          return
        }
        scheduleReconnect()
      }
    }

    void connect()
    return () => {
      disposed = true
      clearTimeout(retry)
      const socket = socketRef.current
      socketRef.current = null
      socket?.close()
      setConnected(false)
      setError(null)
      setFrame(null)
      setMetadata(null)
      setUrl(null)
    }
  }, [threadId, tabId])

  const send = useCallback((message: ViewerMessage) => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify(message))
    return true
  }, [])

  return { frame, metadata, url, connected, error, send }
}
