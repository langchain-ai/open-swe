import { readFile } from "node:fs/promises"
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http"

import type { Backend } from "../backend/types.ts"
import { createReplayGuard, type ReplayGuard, verifyRequest } from "./auth.ts"
import { Router, type WorkstationLogger } from "./routes.ts"
import type { JsonObject } from "./wire.ts"

export type { WorkstationLogger } from "./routes.ts"

export interface WorkstationServerOptions {
  readonly backends: readonly Backend[]
  readonly secret: string
  readonly host?: string
  readonly port?: number
  readonly maxBodyBytes?: number
  readonly maxSkewSeconds?: number
  readonly logger?: WorkstationLogger
  readonly keepaliveIntervalMs?: number
  readonly allowNonLoopbackHost?: boolean
}

export interface WorkstationServer {
  listen(): Promise<{ readonly host: string; readonly port: number }>
  close(): Promise<void>
  readonly address: { readonly host: string; readonly port: number } | null
}

const DEFAULT_HOST = "127.0.0.1"
const DEFAULT_PORT = 8787
const DEFAULT_MAX_BODY_BYTES = 33_554_432
const DEFAULT_MAX_SKEW_SECONDS = 300
const DEFAULT_KEEPALIVE_INTERVAL_MS = 10_000
const TIMESTAMP_HEADER = "x-workstation-timestamp"
const SIGNATURE_HEADER = "x-workstation-signature"
const HEADERS_TIMEOUT_MS = 30_000

/**
 * The tunnel closes a request after an hour and moves roughly 0.4 MiB/s, so a
 * body at the 32 MiB cap legitimately takes over a minute to arrive; Node's
 * 5 minute default would also cut a long `execute` short.
 */
const REQUEST_TIMEOUT_MS = 3_600_000

const LOOPBACK_IPV4 = /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function isLoopbackHost(host: string): boolean {
  if (host === "localhost" || host === "::1" || host === "[::1]") return true
  return LOOPBACK_IPV4.test(host)
}

function singleHeader(
  request: IncomingMessage,
  name: string
): string | undefined {
  const value = request.headers[name]
  return typeof value === "string" ? value : undefined
}

function pathnameOf(target: string): string {
  const end = target.search(/[?#]/)
  return end === -1 ? target : target.slice(0, end)
}

const defaultLogger: WorkstationLogger = {
  info(message, fields) {
    process.stdout.write(
      `${JSON.stringify({ level: "info", message, ...fields })}\n`
    )
  },
  warn(message, fields) {
    process.stderr.write(
      `${JSON.stringify({ level: "warn", message, ...fields })}\n`
    )
  },
}

async function packageVersion(logger: WorkstationLogger): Promise<string> {
  try {
    const raw = await readFile(
      new URL("../../package.json", import.meta.url),
      "utf8"
    )
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed === "object" && parsed !== null) {
      const version = (parsed as { readonly version?: unknown }).version
      if (typeof version === "string") return version
    }
    logger.warn("workstation package version missing", {})
  } catch (error) {
    logger.warn("workstation package version unreadable", {
      error: errorText(error),
    })
  }
  return "0.0.0"
}

function timer(delayMs: number): {
  readonly elapsed: Promise<"keepalive">
  cancel(): void
} {
  let handle: NodeJS.Timeout | undefined
  const elapsed = new Promise<"keepalive">((resolve) => {
    handle = setTimeout(() => resolve("keepalive"), delayMs)
    handle.unref()
  })
  return {
    elapsed,
    cancel(): void {
      if (handle !== undefined) clearTimeout(handle)
    },
  }
}

/**
 * The tunnel drops a connection that stays silent, so a command that produces
 * no output for a while still has to put bytes on the wire.
 */
async function* withKeepalive(
  events: AsyncIterable<JsonObject>,
  intervalMs: number,
  logger: WorkstationLogger
): AsyncGenerator<JsonObject> {
  const iterator = events[Symbol.asyncIterator]()
  let pending: Promise<IteratorResult<JsonObject>> | undefined
  try {
    pending = iterator.next()
    for (;;) {
      const tick = timer(intervalMs)
      const settled = await Promise.race([pending, tick.elapsed])
      tick.cancel()
      if (settled === "keepalive") {
        yield { type: "keepalive" }
        continue
      }
      if (settled.done === true) {
        pending = undefined
        return
      }
      yield settled.value
      pending = iterator.next()
    }
  } finally {
    if (pending !== undefined) {
      pending.then(undefined, (error: unknown) => {
        logger.warn("workstation execute stream abandoned", {
          error: errorText(error),
        })
      })
    }
    await iterator.return?.()
  }
}

class HttpWorkstationServer implements WorkstationServer {
  private readonly router: Router
  private readonly logger: WorkstationLogger
  private readonly secret: string
  private readonly host: string
  private readonly port: number
  private readonly maxBodyBytes: number
  private readonly maxSkewSeconds: number
  private readonly keepaliveIntervalMs: number
  private readonly allowNonLoopbackHost: boolean
  private readonly roots: readonly string[]
  private readonly replay: ReplayGuard
  private readonly server: Server
  private current: { readonly host: string; readonly port: number } | null =
    null

  constructor(options: WorkstationServerOptions) {
    this.logger = options.logger ?? defaultLogger
    this.secret = options.secret
    this.host = options.host ?? DEFAULT_HOST
    this.port = options.port ?? DEFAULT_PORT
    this.maxBodyBytes = options.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES
    this.maxSkewSeconds = options.maxSkewSeconds ?? DEFAULT_MAX_SKEW_SECONDS
    this.keepaliveIntervalMs =
      options.keepaliveIntervalMs ?? DEFAULT_KEEPALIVE_INTERVAL_MS
    this.allowNonLoopbackHost = options.allowNonLoopbackHost ?? false
    this.roots = options.backends.map((backend) => backend.rootDir)
    this.replay = createReplayGuard(
      options.maxSkewSeconds ?? DEFAULT_MAX_SKEW_SECONDS
    )
    this.router = new Router({
      backends: options.backends,
      logger: this.logger,
      version: packageVersion(this.logger),
    })
    this.server = createServer((request, response) => {
      this.accept(request, response)
    })
    this.server.headersTimeout = HEADERS_TIMEOUT_MS
    this.server.requestTimeout = REQUEST_TIMEOUT_MS
  }

  get address(): { readonly host: string; readonly port: number } | null {
    return this.current
  }

  async listen(): Promise<{ readonly host: string; readonly port: number }> {
    if (!isLoopbackHost(this.host) && !this.allowNonLoopbackHost) {
      throw new Error(
        "refusing to bind a non-loopback host: the tunnel connector publishes this server, so set allowNonLoopbackHost to override"
      )
    }
    await new Promise<void>((resolve, reject) => {
      const onError = (error: Error): void => reject(error)
      this.server.once("error", onError)
      this.server.listen({ host: this.host, port: this.port }, () => {
        this.server.off("error", onError)
        resolve()
      })
    })
    const address = this.server.address()
    if (address === null || typeof address === "string") {
      throw new Error("workstation server is not listening on a TCP address")
    }
    this.current = { host: address.address, port: address.port }
    this.logger.info("workstation server listening", {
      host: this.current.host,
      port: this.current.port,
      roots: this.roots,
    })
    return this.current
  }

  async close(): Promise<void> {
    this.current = null
    if (!this.server.listening) return
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => {
        if (error === undefined || error === null) resolve()
        else reject(error)
      })
      this.server.closeAllConnections()
    })
  }

  private accept(request: IncomingMessage, response: ServerResponse): void {
    this.respond(request, response).catch((error: unknown) => {
      this.logger.warn("workstation request failed", {
        method: request.method ?? "",
        path: request.url ?? "",
        error: errorText(error),
      })
      if (response.headersSent) response.destroy()
      else this.sendJson(response, 500, { error: "internal_error" })
    })
  }

  private async respond(
    request: IncomingMessage,
    response: ServerResponse
  ): Promise<void> {
    const method = request.method ?? ""
    const target = request.url ?? ""
    const body = await this.readBody(request)
    if (body === "too_large") {
      this.logger.warn("workstation request body too large", {
        method,
        path: target,
        maxBodyBytes: this.maxBodyBytes,
      })
      this.sendJson(
        response,
        413,
        { error: "payload_too_large" },
        { connection: "close" },
        () => {
          request.destroy()
        }
      )
      return
    }

    const verified = verifyRequest(
      {
        secret: this.secret,
        maxSkewSeconds: this.maxSkewSeconds,
        replay: this.replay,
      },
      {
        method,
        path: target,
        body,
        timestamp: singleHeader(request, TIMESTAMP_HEADER) ?? "",
        signature: singleHeader(request, SIGNATURE_HEADER) ?? "",
      }
    )
    if (!verified.ok) {
      this.logger.warn("workstation request unauthorized", {
        reason: verified.reason,
        method,
        path: target,
        remote: request.socket.remoteAddress ?? "",
      })
      this.sendJson(response, 401, { error: "unauthorized" })
      return
    }

    const result = await this.router.handle(method, pathnameOf(target), body)
    if (result.kind === "json") {
      this.sendJson(response, result.status, result.body, result.headers)
      return
    }
    await this.streamNdjson(response, result.events)
  }

  /**
   * Overflow is detected chunk by chunk and drops what was already collected,
   * so a caller cannot make the process hold a body larger than the cap.
   */
  private async readBody(
    request: IncomingMessage
  ): Promise<Uint8Array | "too_large"> {
    const declared = singleHeader(request, "content-length")
    if (declared !== undefined) {
      const length = Number(declared)
      if (Number.isFinite(length) && length > this.maxBodyBytes) {
        return "too_large"
      }
    }

    const chunks: Buffer[] = []
    let total = 0
    let overflow = false
    await new Promise<void>((resolve, reject) => {
      const onData = (chunk: Buffer): void => {
        total += chunk.byteLength
        if (total > this.maxBodyBytes) {
          overflow = true
          chunks.length = 0
          request.off("data", onData)
          request.pause()
          resolve()
          return
        }
        chunks.push(chunk)
      }
      request.on("data", onData)
      request.once("end", resolve)
      request.once("aborted", resolve)
      request.once("error", reject)
    })
    if (overflow) return "too_large"
    return new Uint8Array(Buffer.concat(chunks))
  }

  private sendJson(
    response: ServerResponse,
    status: number,
    body: JsonObject,
    headers?: Readonly<Record<string, string>>,
    onFlushed?: () => void
  ): void {
    if (response.headersSent || response.destroyed) return
    const payload = Buffer.from(JSON.stringify(body), "utf8")
    response.writeHead(status, {
      ...headers,
      "content-type": "application/json",
      "content-length": payload.byteLength,
    })
    response.end(payload, () => {
      onFlushed?.()
    })
  }

  private async streamNdjson(
    response: ServerResponse,
    events: AsyncIterable<JsonObject>
  ): Promise<void> {
    response.writeHead(200, {
      "content-type": "application/x-ndjson",
      "cache-control": "no-store",
    })
    response.flushHeaders()
    for await (const event of withKeepalive(
      events,
      this.keepaliveIntervalMs,
      this.logger
    )) {
      if (response.writableEnded || response.destroyed) break
      const flushed = response.write(`${JSON.stringify(event)}\n`)
      if (!flushed) await HttpWorkstationServer.drain(response)
    }
    if (!response.writableEnded && !response.destroyed) response.end()
  }

  private static async drain(response: ServerResponse): Promise<void> {
    await new Promise<void>((resolve) => {
      const done = (): void => {
        response.off("drain", done)
        response.off("close", done)
        resolve()
      }
      response.once("drain", done)
      response.once("close", done)
    })
  }
}

export function createWorkstationServer(
  options: WorkstationServerOptions
): WorkstationServer {
  if (options.secret.length === 0) {
    throw new Error("workstation server requires a non-empty secret")
  }
  if (options.backends.length === 0) {
    throw new Error("workstation server requires at least one backend")
  }
  if (options.maxBodyBytes !== undefined && options.maxBodyBytes <= 0) {
    throw new Error("workstation server maxBodyBytes must be positive")
  }
  return new HttpWorkstationServer(options)
}
