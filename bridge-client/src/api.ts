import { arrayAt, isRecord, numberAt, recordAt, stringAt } from "./json"
import type { JsonObject } from "./json"

/** Which app is serving the bridge; the agent owes only the CLI a printable result. */
export type BridgeClient = "cli" | "desktop"

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
  client: BridgeClient
  rootPath: string
  hostname: string
  label: string | null
  bridgeId: string | null
}

export type BridgeReply = { result: JsonObject } | { error: string }

export interface PollOptions {
  wait: number
  limit: number
  held: readonly string[]
  signal?: AbortSignal
}

/** The backend's `/api/bridges` routes, as the machine side calls them. */
export interface BridgeApi {
  createBridge(input: CreateBridgeInput): Promise<BridgeSession>
  heartbeat(bridgeId: string): Promise<void>
  /** Long-poll for requests, naming the ones already running so a lost response is re-offered. */
  pollRequests(bridgeId: string, options: PollOptions): Promise<BridgeRequest[]>
  respond(
    bridgeId: string,
    requestId: string,
    reply: BridgeReply
  ): Promise<void>
  deleteBridge(bridgeId: string): Promise<void>
}

/**
 * Sends one request to `<backend>/api<path>` and resolves to its
 * parsed JSON body, or rejects with an error carrying the HTTP `status`.
 */
export type BridgeSender = (
  method: "POST" | "DELETE",
  path: string,
  options: { body?: JsonObject; signal?: AbortSignal }
) => Promise<unknown>

export class BridgeProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "BridgeProtocolError"
  }
}

/** The HTTP status an API error carries, for errors from any client's sender. */
export function httpStatus(cause: unknown): number | null {
  if (!isRecord(cause)) return null
  const status = cause["status"]
  return typeof status === "number" ? status : null
}

function bridgePath(bridgeId: string, ...rest: string[]): string {
  return ["/bridges", bridgeId, ...rest].map(encodeSegment).join("/")
}

function encodeSegment(segment: string, index: number): string {
  return index === 0 ? segment : encodeURIComponent(segment)
}

export class BridgeHttpApi implements BridgeApi {
  constructor(private readonly send: BridgeSender) {}

  async createBridge(input: CreateBridgeInput): Promise<BridgeSession> {
    const payload = await this.send("POST", "/bridges", {
      body: {
        client: input.client,
        root_path: input.rootPath,
        hostname: input.hostname,
        label: input.label,
        bridge_id: input.bridgeId,
      },
    })
    if (!isRecord(payload))
      throw new BridgeProtocolError("bridge response is not an object")
    const bridgeId = stringAt(payload, "bridge_id")
    if (!bridgeId)
      throw new BridgeProtocolError("bridge response is missing bridge_id")
    return {
      bridgeId,
      heartbeatIntervalSeconds:
        numberAt(payload, "heartbeat_interval_seconds") ?? 15,
      aliveThresholdSeconds: numberAt(payload, "alive_threshold_seconds") ?? 60,
    }
  }

  async heartbeat(bridgeId: string): Promise<void> {
    await this.send("POST", bridgePath(bridgeId, "heartbeat"), {})
  }

  async pollRequests(
    bridgeId: string,
    options: PollOptions
  ): Promise<BridgeRequest[]> {
    const payload = await this.send(
      "POST",
      bridgePath(bridgeId, "requests", "claim"),
      {
        body: {
          wait: options.wait,
          limit: options.limit,
          held: [...options.held],
        },
        ...(options.signal ? { signal: options.signal } : {}),
      }
    )
    const entries = arrayAt(isRecord(payload) ? payload : null, "requests")
    if (entries === null) {
      throw new BridgeProtocolError("poll response is missing a requests array")
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
    await this.send("POST", bridgePath(bridgeId, "requests", requestId), {
      body: reply,
    })
  }

  async deleteBridge(bridgeId: string): Promise<void> {
    await this.send("DELETE", bridgePath(bridgeId), {})
  }
}
