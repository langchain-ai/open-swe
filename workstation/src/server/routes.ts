import path from "node:path"

import { isInside } from "../backend/paths.ts"
import type { Backend } from "../backend/types.ts"
import {
  encodeDownloadResponses,
  encodeEditResult,
  encodeExecuteEvent,
  encodeGlobResult,
  encodeGrepResult,
  encodeLsResult,
  encodePathResult,
  encodeReadResult,
  encodeUploadResponses,
  type ExecuteRequest,
  type JsonObject,
  parseDeleteRequest,
  parseDownloadRequest,
  parseEditRequest,
  parseExecuteRequest,
  parseGlobRequest,
  parseGrepRequest,
  parseLsRequest,
  parseReadRequest,
  parseUploadRequest,
  parseWriteRequest,
  WireError,
} from "./wire.ts"

export interface WorkstationLogger {
  info(message: string, fields?: Readonly<Record<string, unknown>>): void
  warn(message: string, fields?: Readonly<Record<string, unknown>>): void
}

export type RouteResult =
  | {
      readonly kind: "json"
      readonly status: number
      readonly body: JsonObject
      readonly headers?: Readonly<Record<string, string>>
    }
  | { readonly kind: "ndjson"; readonly events: AsyncIterable<JsonObject> }

export interface RouterOptions {
  readonly backends: readonly Backend[]
  readonly logger: WorkstationLogger
  readonly version: Promise<string>
}

type Handler = (body: unknown) => Promise<RouteResult>

class RouteFailure extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string) {
    super(code)
    this.name = "RouteFailure"
    this.status = status
    this.code = code
  }
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function json(status: number, body: JsonObject): RouteResult {
  return { kind: "json", status, body }
}

export class Router {
  private readonly backends: readonly Backend[]
  private readonly logger: WorkstationLogger
  private readonly version: Promise<string>
  private readonly handlers: ReadonlyMap<string, Handler>

  constructor(options: RouterOptions) {
    this.backends = options.backends
    this.logger = options.logger
    this.version = options.version
    this.handlers = new Map<string, Handler>([
      ["/v1/fs/ls", (body) => this.ls(body)],
      ["/v1/fs/read", (body) => this.read(body)],
      ["/v1/fs/write", (body) => this.write(body)],
      ["/v1/fs/edit", (body) => this.edit(body)],
      ["/v1/fs/delete", (body) => this.delete(body)],
      ["/v1/fs/grep", (body) => this.grep(body)],
      ["/v1/fs/glob", (body) => this.glob(body)],
      ["/v1/fs/upload", (body) => this.upload(body)],
      ["/v1/fs/download", (body) => this.download(body)],
      ["/v1/execute", (body) => this.execute(body)],
    ])
  }

  async handle(
    method: string,
    pathname: string,
    rawBody: Uint8Array
  ): Promise<RouteResult> {
    if (pathname === "/v1/health") {
      if (method !== "GET") return Router.methodNotAllowed("GET")
      return json(200, {
        ok: true,
        version: await this.version,
        roots: this.backends.map((backend) => backend.rootDir),
      })
    }

    const handler = this.handlers.get(pathname)
    if (handler === undefined) {
      return json(404, { error: "not_found" })
    }
    if (method !== "POST") return Router.methodNotAllowed("POST")

    let body: unknown
    try {
      body = JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(rawBody)
      )
    } catch (error) {
      this.logger.warn("workstation request body rejected", {
        route: pathname,
        error: errorText(error),
      })
      return Router.invalidRequest("body must be valid JSON")
    }

    try {
      return await handler(body)
    } catch (error) {
      if (error instanceof WireError) {
        return Router.invalidRequest(error.message)
      }
      if (error instanceof RouteFailure) {
        return json(error.status, { error: error.code })
      }
      this.logger.warn("workstation route failed", {
        route: pathname,
        error: errorText(error),
      })
      return json(500, { error: "internal_error" })
    }
  }

  private static methodNotAllowed(allow: string): RouteResult {
    return {
      kind: "json",
      status: 405,
      body: { error: "method_not_allowed" },
      headers: { allow },
    }
  }

  private static invalidRequest(detail: string): RouteResult {
    return json(400, { error: "invalid_request", detail })
  }

  /**
   * A request names the root it targets; the backend that owns it is the one
   * with the longest `rootDir` containing that path, so a root nested inside
   * another still reaches its own backend.
   */
  private backendFor(root: string): Backend {
    const target = path.resolve(root)
    let owner: Backend | undefined
    let ownerLength = -1
    for (const backend of this.backends) {
      const rootDir = path.resolve(backend.rootDir)
      if (isInside(rootDir, target) && rootDir.length > ownerLength) {
        owner = backend
        ownerLength = rootDir.length
      }
    }
    if (owner === undefined) {
      throw new RouteFailure(404, "unknown_root")
    }
    return owner
  }

  private async ls(body: unknown): Promise<RouteResult> {
    const request = parseLsRequest(body)
    const backend = this.backendFor(request.root)
    return json(200, encodeLsResult(await backend.ls(request.path)))
  }

  private async read(body: unknown): Promise<RouteResult> {
    const request = parseReadRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodeReadResult(await backend.read(request.filePath, request.options))
    )
  }

  private async write(body: unknown): Promise<RouteResult> {
    const request = parseWriteRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodePathResult(await backend.write(request.filePath, request.content))
    )
  }

  private async edit(body: unknown): Promise<RouteResult> {
    const request = parseEditRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodeEditResult(
        await backend.edit(
          request.filePath,
          request.oldString,
          request.newString,
          request.replaceAll
        )
      )
    )
  }

  private async delete(body: unknown): Promise<RouteResult> {
    const request = parseDeleteRequest(body)
    const backend = this.backendFor(request.root)
    return json(200, encodePathResult(await backend.delete(request.filePath)))
  }

  private async grep(body: unknown): Promise<RouteResult> {
    const request = parseGrepRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodeGrepResult(await backend.grep(request.pattern, request.options))
    )
  }

  private async glob(body: unknown): Promise<RouteResult> {
    const request = parseGlobRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodeGlobResult(await backend.glob(request.pattern, request.path))
    )
  }

  private async upload(body: unknown): Promise<RouteResult> {
    const request = parseUploadRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodeUploadResponses(await backend.uploadFiles(request.files))
    )
  }

  private async download(body: unknown): Promise<RouteResult> {
    const request = parseDownloadRequest(body)
    const backend = this.backendFor(request.root)
    return json(
      200,
      encodeDownloadResponses(await backend.downloadFiles(request.paths))
    )
  }

  private async execute(body: unknown): Promise<RouteResult> {
    const request = parseExecuteRequest(body)
    const backend = this.backendFor(request.root)
    return { kind: "ndjson", events: this.executeEvents(backend, request) }
  }

  /**
   * A failure after the first event cannot change the status code, so it is
   * reported as a terminal stream event instead: a consumer that sees no `exit`
   * must treat the command as failed.
   */
  private async *executeEvents(
    backend: Backend,
    request: ExecuteRequest
  ): AsyncGenerator<JsonObject> {
    try {
      for await (const event of backend.executeStream(
        request.command,
        request.options
      )) {
        yield encodeExecuteEvent(event)
      }
    } catch (error) {
      this.logger.warn("workstation execute stream failed", {
        error: errorText(error),
      })
      yield { type: "error", message: "execute stream failed" }
    }
  }
}
