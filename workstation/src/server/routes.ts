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
  readonly backend: Backend
  readonly logger: WorkstationLogger
  readonly version: Promise<string>
}

type Handler = (body: unknown) => Promise<RouteResult>

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function json(status: number, body: JsonObject): RouteResult {
  return { kind: "json", status, body }
}

export class Router {
  private readonly backend: Backend
  private readonly logger: WorkstationLogger
  private readonly version: Promise<string>
  private readonly handlers: ReadonlyMap<string, Handler>

  constructor(options: RouterOptions) {
    this.backend = options.backend
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
        default_dir: this.backend.defaultDir,
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

  private async ls(body: unknown): Promise<RouteResult> {
    const request = parseLsRequest(body)
    return json(200, encodeLsResult(await this.backend.ls(request.path)))
  }

  private async read(body: unknown): Promise<RouteResult> {
    const request = parseReadRequest(body)
    return json(
      200,
      encodeReadResult(
        await this.backend.read(request.filePath, request.options)
      )
    )
  }

  private async write(body: unknown): Promise<RouteResult> {
    const request = parseWriteRequest(body)
    return json(
      200,
      encodePathResult(
        await this.backend.write(request.filePath, request.content)
      )
    )
  }

  private async edit(body: unknown): Promise<RouteResult> {
    const request = parseEditRequest(body)
    return json(
      200,
      encodeEditResult(
        await this.backend.edit(
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
    return json(
      200,
      encodePathResult(await this.backend.delete(request.filePath))
    )
  }

  private async grep(body: unknown): Promise<RouteResult> {
    const request = parseGrepRequest(body)
    return json(
      200,
      encodeGrepResult(
        await this.backend.grep(request.pattern, request.options)
      )
    )
  }

  private async glob(body: unknown): Promise<RouteResult> {
    const request = parseGlobRequest(body)
    return json(
      200,
      encodeGlobResult(await this.backend.glob(request.pattern, request.path))
    )
  }

  private async upload(body: unknown): Promise<RouteResult> {
    const request = parseUploadRequest(body)
    return json(
      200,
      encodeUploadResponses(await this.backend.uploadFiles(request.files))
    )
  }

  private async download(body: unknown): Promise<RouteResult> {
    const request = parseDownloadRequest(body)
    return json(
      200,
      encodeDownloadResponses(await this.backend.downloadFiles(request.paths))
    )
  }

  private async execute(body: unknown): Promise<RouteResult> {
    const request = parseExecuteRequest(body)
    return { kind: "ndjson", events: this.executeEvents(request) }
  }

  /**
   * A failure after the first event cannot change the status code, so it is
   * reported as a terminal stream event instead: a consumer that sees no `exit`
   * must treat the command as failed.
   */
  private async *executeEvents(
    request: ExecuteRequest
  ): AsyncGenerator<JsonObject> {
    try {
      for await (const event of this.backend.executeStream(
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
