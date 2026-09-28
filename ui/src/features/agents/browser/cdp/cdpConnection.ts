/**
 * Chrome DevTools Protocol client over the backend's sandbox browser bridge.
 *
 * The websocket carries two kinds of text frame: control messages from the
 * bridge itself (an object with a string `type`), and raw CDP messages, which
 * never have a `type` field. Requests are matched to responses by `id`; events
 * fan out by method name and carry the flattened `sessionId` of the target
 * they belong to.
 */

export interface CloudBrowserEndpoint {
  readonly url: string
  readonly protocol: string
  readonly ticket: string
}

export type CdpControlMessage =
  | { readonly type: "ready" }
  | { readonly type: "browser-missing"; readonly installCommand?: string }
  | { readonly type: "installing" }
  | { readonly type: "install-output"; readonly data: string }
  | { readonly type: "error"; readonly message: string }

export interface CdpErrorPayload {
  readonly code: number
  readonly message: string
  readonly data?: string
}

interface CdpIncomingMessage {
  readonly id?: number
  readonly method?: string
  readonly params?: unknown
  readonly sessionId?: string
  readonly result?: unknown
  readonly error?: CdpErrorPayload
}

export class CdpProtocolError extends Error {
  override readonly name = "CdpProtocolError"

  constructor(
    readonly method: string,
    readonly payload: CdpErrorPayload
  ) {
    super(
      `${method}: ${payload.message}${payload.data ? ` (${payload.data})` : ""}`
    )
  }
}

export class CdpDisconnectedError extends Error {
  override readonly name = "CdpDisconnectedError"

  constructor(reason = "The browser connection closed.") {
    super(reason)
  }
}

export type CdpEventHandler = (
  params: unknown,
  sessionId: string | undefined
) => void

export interface CdpConnectionCallbacks {
  /** The bridge finished attaching to the sandbox browser; targets are usable. */
  readonly onReady: () => void
  readonly onControl: (message: CdpControlMessage) => void
  readonly onEvent: (
    method: string,
    params: unknown,
    sessionId: string | undefined
  ) => void
  /** A socket closed; `willRetry` says whether a reconnect is scheduled. */
  readonly onClose: (willRetry: boolean, reason: string | null) => void
  readonly onConnecting: (attempt: number) => void
}

export const MAX_CDP_RECONNECT_ATTEMPTS = 5
const MAX_INBOUND_FRAME_BYTES = 64 * 1024 * 1024

export function cdpReconnectDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 10_000)
}

/** Policy-violation and normal closes are final; everything else retries. */
export function shouldReconnectCdp(code: number, attempt: number): boolean {
  if (code === 1000 || code === 1008) return false
  return attempt < MAX_CDP_RECONNECT_ATTEMPTS
}

/**
 * Classifies one inbound text frame. Control messages are the bridge's own;
 * everything else is a CDP message to route by id or method.
 */
export function classifyBridgeFrame(
  raw: string
):
  | { kind: "control"; message: CdpControlMessage }
  | { kind: "cdp"; message: CdpIncomingMessage }
  | null {
  if (raw.length > MAX_INBOUND_FRAME_BYTES) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== "object") return null
  const record = parsed as Record<string, unknown>
  if (typeof record.type === "string") {
    return isControlMessage(record)
      ? { kind: "control", message: record }
      : null
  }
  return { kind: "cdp", message: record as CdpIncomingMessage }
}

function isControlMessage(
  record: Record<string, unknown>
): record is CdpControlMessage & Record<string, unknown> {
  switch (record.type) {
    case "ready":
    case "installing":
      return true
    case "browser-missing":
      return (
        record.installCommand === undefined ||
        typeof record.installCommand === "string"
      )
    case "install-output":
      return typeof record.data === "string"
    case "error":
      return typeof record.message === "string"
    default:
      return false
  }
}

interface PendingRequest {
  readonly method: string
  readonly resolve: (value: unknown) => void
  readonly reject: (error: Error) => void
}

export class CdpConnection {
  private socket: WebSocket | null = null
  private nextId = 1
  private readonly pending = new Map<number, PendingRequest>()
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private retryCount = 0
  private disposed = false
  private ready = false

  constructor(
    private readonly connect: () => Promise<CloudBrowserEndpoint>,
    private readonly callbacks: CdpConnectionCallbacks
  ) {}

  get isReady(): boolean {
    return this.ready
  }

  start(): void {
    if (this.disposed || this.socket || this.retryTimer) return
    void this.open()
  }

  dispose(): void {
    this.disposed = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    const socket = this.socket
    this.socket = null
    this.ready = false
    socket?.close(1000)
    this.rejectPending(new CdpDisconnectedError())
  }

  /** Asks the bridge to install Chromium into the sandbox. */
  requestInstall(): void {
    this.sendRaw({ type: "install" })
  }

  send<T = unknown>(
    method: string,
    params?: object,
    sessionId?: string
  ): Promise<T> {
    const socket = this.socket
    if (!socket || socket.readyState !== WebSocket.OPEN || !this.ready) {
      return Promise.reject(
        new CdpDisconnectedError("The browser is not connected.")
      )
    }
    const id = this.nextId++
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        method,
        resolve: (value) => resolve(value as T),
        reject,
      })
      socket.send(
        JSON.stringify({
          id,
          method,
          ...(params ? { params } : {}),
          ...(sessionId ? { sessionId } : {}),
        })
      )
    })
  }

  private sendRaw(message: object): void {
    const socket = this.socket
    if (socket?.readyState === WebSocket.OPEN)
      socket.send(JSON.stringify(message))
  }

  private async open(): Promise<void> {
    this.callbacks.onConnecting(this.retryCount)
    let endpoint: CloudBrowserEndpoint
    try {
      endpoint = await this.connect()
    } catch (error: unknown) {
      if (this.disposed) return
      this.scheduleRetry(
        error instanceof Error
          ? error.message
          : "Unable to reach the browser bridge."
      )
      return
    }
    if (this.disposed) return
    const socket = new WebSocket(endpoint.url, [
      endpoint.protocol,
      endpoint.ticket,
    ])
    this.socket = socket
    socket.onmessage = (event) => {
      if (this.socket !== socket || typeof event.data !== "string") return
      this.handleFrame(event.data)
    }
    socket.onclose = (event) => {
      if (this.socket !== socket) return
      this.socket = null
      const wasReady = this.ready
      this.ready = false
      this.rejectPending(new CdpDisconnectedError(event.reason || undefined))
      if (this.disposed) return
      const retry = shouldReconnectCdp(event.code, this.retryCount)
      this.callbacks.onClose(retry, event.reason || null)
      if (retry) {
        // A connection that had become ready resets the budget: the failure
        // was a drop, not a refusal.
        if (wasReady) this.retryCount = 0
        this.scheduleRetry(null)
      }
    }
    socket.onerror = () => {
      // The close event that follows carries the code and reason.
    }
  }

  private scheduleRetry(reason: string | null): void {
    if (this.disposed || this.retryTimer) return
    if (!shouldReconnectCdp(1006, this.retryCount)) {
      this.callbacks.onClose(false, reason)
      return
    }
    const delay = cdpReconnectDelay(this.retryCount++)
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.open()
    }, delay)
  }

  private handleFrame(raw: string): void {
    const frame = classifyBridgeFrame(raw)
    if (!frame) return
    if (frame.kind === "control") {
      if (frame.message.type === "ready") {
        this.ready = true
        this.retryCount = 0
        this.callbacks.onReady()
      }
      this.callbacks.onControl(frame.message)
      return
    }
    const message = frame.message
    if (typeof message.id === "number") {
      const request = this.pending.get(message.id)
      if (!request) return
      this.pending.delete(message.id)
      if (message.error)
        request.reject(new CdpProtocolError(request.method, message.error))
      else request.resolve(message.result)
      return
    }
    if (typeof message.method === "string") {
      this.callbacks.onEvent(message.method, message.params, message.sessionId)
    }
  }

  private rejectPending(error: Error): void {
    for (const request of this.pending.values()) request.reject(error)
    this.pending.clear()
  }
}
