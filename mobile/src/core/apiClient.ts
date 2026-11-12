import { isObject, stringAt } from "./models"
import { ServerSentEventParser, type ServerSentEvent } from "./serverSentEvents"

export class APIError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(status === 0 ? detail : `HTTP ${status}: ${detail}`)
    this.name = "APIError"
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  /** What to show a person for any thrown value. */
  static describe(error: unknown): string {
    if (error instanceof Error) return error.message
    if (isObject(error))
      return stringAt(error, "message") ?? "Something went wrong."
    return String(error)
  }

  /** The error for a non-2xx answer, using the body's `detail` or `message` when it has one. */
  static fromResponse(body: string, status: number): APIError {
    try {
      const json: unknown = JSON.parse(body)
      const detail = stringAt(json, "detail") ?? stringAt(json, "message")
      if (detail) return new APIError(status, detail.slice(0, 600))
    } catch {
      // Not JSON; fall through to the raw text.
    }
    const text = body.slice(0, 600).trim()
    return new APIError(status, text || `Request failed with status ${status}`)
  }
}

type Query = Record<string, string>

/** How an event stream finished without failing. */
export type StreamEnd = "closed" | "rotated"

export class InsecureBackendError extends Error {
  constructor() {
    super("The backend must use https (http is allowed only for localhost).")
  }
}

/**
 * Client for `<backend>/dashboard/api`, signed in with a dashboard session.
 *
 * The session JWT goes out as the dashboard's own cookie, with the backend's
 * own `Origin`, the way the CLI and desktop app send it. Requests omit
 * credentials, so the system cookie store stays out of it; on a redirect,
 * React Native rebuilds the request's headers from that store alone, so the
 * session never follows a redirect to another host.
 */
export class APIClient {
  /**
   * How much of a live event stream to read before reopening it. The response
   * text keeps every byte received, so a long-lived stream is rotated rather
   * than left to grow. Rotation waits for a frame boundary, so a single frame
   * longer than this still arrives whole.
   */
  static readonly streamRotationLength = 1_000_000

  constructor(
    readonly backend: string,
    private readonly sessionToken: string
  ) {}

  /** Reduce what someone typed to a backend origin, refusing plain http off this machine. */
  static origin(input: string): string {
    let text = input.trim().replace(/\/+$/, "")
    if (!/^https?:\/\//i.test(text)) text = `https://${text}`
    const match = /^(https?):\/\/(\[[^\]]+\]|[^/:?#\s]+)(?::(\d+))?\/?$/i.exec(
      text
    )
    if (!match) throw new APIError(0, "The backend URL is not valid.")
    const scheme = match[1].toLowerCase()
    const host = match[2].toLowerCase()
    if (
      scheme !== "https" &&
      !(scheme === "http" && APIClient.isLocalHost(host))
    )
      throw new InsecureBackendError()
    return `${scheme}://${host}${match[3] ? `:${match[3]}` : ""}`
  }

  /** Whether an origin such as `http://127.0.0.1:2024` is on this machine. */
  static isLocalOrigin(origin: string): boolean {
    const authority = origin.replace(/^https?:\/\//i, "")
    const host = authority.startsWith("[")
      ? authority.slice(0, authority.indexOf("]") + 1)
      : authority.split(":")[0]
    return APIClient.isLocalHost(host)
  }

  private static isLocalHost(host: string): boolean {
    const lower = host.toLowerCase()
    return (
      ["localhost", "127.0.0.1", "::1", "[::1]"].includes(lower) ||
      lower.endsWith(".localhost")
    )
  }

  url(path: string, query?: Query): string {
    const parts = Object.entries(query ?? {}).map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(value)}`
    )
    const search = parts.length ? `?${parts.join("&")}` : ""
    return `${this.backend}/dashboard/api${path}${search}`
  }

  private headers(accept: string, json: boolean): Record<string, string> {
    return {
      Accept: accept,
      Cookie: `osw_session=${this.sessionToken}`,
      Origin: this.backend,
      ...(json ? { "Content-Type": "application/json" } : {}),
    }
  }

  async get(path: string, query?: Query): Promise<unknown> {
    const text = await this.send("GET", path, { query })
    return text ? JSON.parse(text) : null
  }

  /** Send a request and return its body text; throws `APIError` on a non-2xx answer. */
  async send(
    method: string,
    path: string,
    options: { query?: Query; json?: unknown } = {}
  ): Promise<string> {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 30_000)
    let response: Response
    try {
      response = await fetch(this.url(path, options.query), {
        method,
        headers: this.headers("application/json", options.json !== undefined),
        body:
          options.json === undefined ? undefined : JSON.stringify(options.json),
        credentials: "omit",
        signal: controller.signal,
      })
    } catch (error) {
      throw new APIError(
        0,
        controller.signal.aborted
          ? "The request timed out."
          : String((error as Error).message ?? error)
      )
    } finally {
      clearTimeout(timeout)
    }
    const text = await response.text()
    if (!response.ok) throw APIError.fromResponse(text, response.status)
    return text
  }

  /**
   * A server-sent event stream, read frame by frame. Resolves `"closed"` when
   * the server ends it or `signal` aborts, and `"rotated"` when it was closed
   * here, between frames, after `streamRotationLength` characters, for the
   * caller to reopen from where it got to. Rejects when the connection fails.
   */
  events(
    path: string,
    query: Query,
    onFrame: (frame: ServerSentEvent) => void,
    signal: AbortSignal
  ): Promise<StreamEnd> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const parser = new ServerSentEventParser()
      let seen = 0
      let rotated = false
      const drain = () => {
        const text = xhr.responseText ?? ""
        if (text.length <= seen) return
        const chunk = text.slice(seen)
        seen = text.length
        for (const frame of parser.push(chunk)) onFrame(frame)
        if (
          seen >= APIClient.streamRotationLength &&
          parser.isBetweenFrames &&
          !rotated
        ) {
          rotated = true
          xhr.abort()
        }
      }
      signal.addEventListener("abort", () => xhr.abort())
      xhr.open("GET", this.url(path, query))
      xhr.withCredentials = false
      for (const [key, value] of Object.entries(
        this.headers("text/event-stream", false)
      )) {
        xhr.setRequestHeader(key, value)
      }
      xhr.onprogress = () => {
        if (xhr.status >= 200 && xhr.status < 300) drain()
      }
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(APIError.fromResponse(xhr.responseText ?? "", xhr.status))
          return
        }
        drain()
        resolve("closed")
      }
      xhr.onerror = () =>
        reject(new APIError(0, "The event stream was interrupted."))
      xhr.onabort = () => resolve(rotated ? "rotated" : "closed")
      xhr.send()
    })
  }
}
