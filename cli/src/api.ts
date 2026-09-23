import {
  arrayAt,
  isRecord,
  numberAt,
  parseJson,
  recordAt,
  stringAt,
  type JsonObject,
} from "./json.ts"
import type { Credential } from "./credentials.ts"

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(`HTTP ${status}: ${detail}`)
    this.name = "ApiError"
  }
}

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "ProtocolError"
  }
}

export interface BridgeSession {
  bridgeId: string
  heartbeatIntervalSeconds: number
  aliveThresholdSeconds: number
}

export interface BridgeRequest {
  requestId: string
  method: string
  params: Record<string, unknown>
}

export interface CreateBridgeInput {
  rootPath: string
  hostname: string
  label: string | null
  bridgeId: string | null
}

export interface EventStreamInput {
  channels: readonly string[]
  namespaces: readonly string[][]
  depth: number
  since: number
}

export type BridgeReply = { result: JsonObject } | { error: string }

const MAX_DETAIL_CHARS = 600

function detailFrom(status: string, body: string): string {
  const parsed = parseJson(body)
  const detail = isRecord(parsed)
    ? (stringAt(parsed, "detail") ?? stringAt(parsed, "message"))
    : null
  const text = (detail ?? body ?? "").trim() || status
  return text.length > MAX_DETAIL_CHARS
    ? `${text.slice(0, MAX_DETAIL_CHARS)}…`
    : text
}

export function normalizeBackend(backend: string): string {
  const trimmed = backend.trim().replace(/\/+$/, "")
  const withScheme = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed}`
  const url = new URL(withScheme)
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error(`backend must be an origin, got ${backend}`)
  }
  return `${url.protocol}//${url.host}`
}

/** Client for `<backend>/dashboard/api`, authenticated by whichever credential it holds. */
export class ApiClient {
  readonly backend: string

  constructor(
    backend: string,
    readonly credential: Credential
  ) {
    this.backend = backend.replace(/\/+$/, "")
  }

  dashboardUrl(path: string): string {
    return `${this.backend}${path}`
  }

  private url(path: string): string {
    return `${this.backend}/dashboard/api${path}`
  }

  private async headers(accept: string): Promise<Record<string, string>> {
    return {
      ...(await this.credential.headers(this.backend)),
      "Content-Type": "application/json",
      Accept: accept,
    }
  }

  private async send(
    method: string,
    path: string,
    options: { body?: unknown; accept?: string; signal?: AbortSignal } = {}
  ): Promise<Response> {
    const response = await fetch(this.url(path), {
      method,
      headers: await this.headers(options.accept ?? "application/json"),
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
    if (!response.ok) {
      throw new ApiError(
        response.status,
        detailFrom(response.statusText, await response.text())
      )
    }
    return response
  }

  private async json(
    method: string,
    path: string,
    options: { body?: unknown; signal?: AbortSignal } = {}
  ): Promise<unknown> {
    const response = await this.send(method, path, options)
    if (response.status === 204) return null
    const text = await response.text()
    return text ? parseJson(text) : null
  }

  async createBridge(input: CreateBridgeInput): Promise<BridgeSession> {
    const payload = await this.json("POST", "/bridges", {
      body: {
        root_path: input.rootPath,
        hostname: input.hostname,
        label: input.label,
        bridge_id: input.bridgeId,
      },
    })
    if (!isRecord(payload))
      throw new ProtocolError("bridge response is not an object")
    const bridgeId = stringAt(payload, "bridge_id")
    if (!bridgeId)
      throw new ProtocolError("bridge response is missing bridge_id")
    return {
      bridgeId,
      heartbeatIntervalSeconds:
        numberAt(payload, "heartbeat_interval_seconds") ?? 15,
      aliveThresholdSeconds: numberAt(payload, "alive_threshold_seconds") ?? 60,
    }
  }

  async heartbeat(bridgeId: string): Promise<void> {
    await this.send(
      "POST",
      `/bridges/${encodeURIComponent(bridgeId)}/heartbeat`
    )
  }

  async pollRequests(
    bridgeId: string,
    options: { wait: number; limit: number; signal?: AbortSignal }
  ): Promise<BridgeRequest[]> {
    const query = `wait=${options.wait}&limit=${options.limit}`
    const payload = await this.json(
      "GET",
      `/bridges/${encodeURIComponent(bridgeId)}/requests?${query}`,
      { signal: options.signal }
    )
    const entries = arrayAt(isRecord(payload) ? payload : null, "requests")
    if (entries === null) {
      throw new ProtocolError("poll response is missing a requests array")
    }
    const requests: BridgeRequest[] = []
    for (const entry of entries) {
      if (!isRecord(entry)) continue
      const requestId = stringAt(entry, "request_id")
      const method = stringAt(entry, "method")
      if (!requestId || !method) continue
      requests.push({
        requestId,
        method,
        params: recordAt(entry, "params") ?? {},
      })
    }
    return requests
  }

  async respond(
    bridgeId: string,
    requestId: string,
    reply: BridgeReply
  ): Promise<void> {
    await this.send(
      "POST",
      `/bridges/${encodeURIComponent(bridgeId)}/requests/${encodeURIComponent(requestId)}`,
      { body: reply }
    )
  }

  async deleteBridge(bridgeId: string): Promise<void> {
    await this.send("DELETE", `/bridges/${encodeURIComponent(bridgeId)}`)
  }

  /** Start a run on `threadId`; returns the run id when the server reports one. */
  async startRun(
    threadId: string,
    configurable: JsonObject,
    prompt: string
  ): Promise<string | null> {
    const payload = await this.json(
      "POST",
      `/threads/${encodeURIComponent(threadId)}/commands`,
      {
        body: {
          id: 1,
          method: "run.start",
          params: {
            input: { messages: [{ type: "human", content: prompt }] },
            config: { configurable },
          },
        },
      }
    )
    if (!isRecord(payload)) return null
    if (stringAt(payload, "type") === "error") {
      const detail =
        stringAt(payload, "message") ??
        stringAt(payload, "error") ??
        "run.start was rejected"
      throw new ProtocolError(detail)
    }
    return (
      stringAt(payload, "run_id") ??
      stringAt(recordAt(payload, "result"), "run_id")
    )
  }

  async cancelThread(threadId: string): Promise<void> {
    await this.send("POST", `/threads/${encodeURIComponent(threadId)}/cancel`)
  }

  async openEventStream(
    threadId: string,
    input: EventStreamInput,
    signal?: AbortSignal
  ): Promise<Response> {
    return await this.send(
      "POST",
      `/threads/${encodeURIComponent(threadId)}/stream/events`,
      { body: input, accept: "text/event-stream", signal }
    )
  }
}

export async function exchangeDesktopHandoff(
  backend: string,
  code: string,
  verifier: string
): Promise<string> {
  const response = await fetch(
    `${backend}/dashboard/api/auth/desktop/exchange`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ code, verifier }),
    }
  )
  const text = await response.text()
  if (!response.ok) {
    throw new ApiError(response.status, detailFrom(response.statusText, text))
  }
  const payload = parseJson(text)
  const session = stringAt(isRecord(payload) ? payload : null, "session")
  if (!session)
    throw new ProtocolError("exchange response is missing a session")
  return session
}
