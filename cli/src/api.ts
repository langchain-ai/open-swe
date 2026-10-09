import {
  isRecord,
  parseJson,
  recordAt,
  stringAt,
  type JsonObject,
} from "./json.ts"
import type { Credential } from "./credentials.ts"
import { BridgeHttpApi } from "open-swe-bridge-client"
import * as z from "zod"

const identitySchema = z.object({
  login: z.string(),
  email: z.string().nullish(),
  is_admin: z.boolean().nullish(),
})

export type Identity = z.infer<typeof identitySchema>

const uploadedThreadSchema = z.object({ id: z.string() })

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

/** A pre-encoded request body and the headers that describe it. */
interface RawBody {
  data: Uint8Array
  headers: Record<string, string>
}

export interface EventStreamInput {
  channels: readonly string[]
  namespaces: readonly string[][]
  depth: number
  since: number
}

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
    options: {
      body?: unknown
      raw?: RawBody
      accept?: string
      signal?: AbortSignal
    } = {}
  ): Promise<Response> {
    const headers = {
      ...(await this.headers(options.accept ?? "application/json")),
      ...options.raw?.headers,
    }
    const response = await fetch(this.url(path), {
      method,
      headers,
      body:
        options.raw?.data ??
        (options.body === undefined ? undefined : JSON.stringify(options.body)),
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
    options: { body?: unknown; raw?: RawBody; signal?: AbortSignal } = {}
  ): Promise<unknown> {
    const response = await this.send(method, path, options)
    if (response.status === 204) return null
    const text = await response.text()
    return text ? parseJson(text) : null
  }

  /** The signed-in person; only a session can ask. */
  async me(): Promise<Identity> {
    const parsed = identitySchema.safeParse(await this.json("GET", "/me"))
    if (!parsed.success) throw new ProtocolError("/me response is malformed")
    return parsed.data
  }

  /** Whether the server accepts this credential, for machines that have no `/me`. */
  async verifyMachine(): Promise<void> {
    await this.send("GET", "/threads?limit=1")
  }

  /** The bridge routes, for `Bridge` to serve this machine through. */
  bridges(): BridgeHttpApi {
    return new BridgeHttpApi((method, path, options) =>
      this.json(method, path, options)
    )
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
        Origin: backend,
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

/** Fill the thread an upload code reserved with a gzipped JSONL transcript; returns its id. */
export async function uploadSession(
  backend: string,
  code: string,
  gzippedJsonl: Uint8Array
): Promise<string> {
  const response = await fetch(`${backend}/dashboard/api/threads/uploads`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${code}`,
      "Content-Type": "application/x-ndjson",
      "Content-Encoding": "gzip",
      Accept: "application/json",
    },
    body: gzippedJsonl,
  })
  const text = await response.text()
  if (!response.ok) {
    throw new ApiError(response.status, detailFrom(response.statusText, text))
  }
  const parsed = uploadedThreadSchema.safeParse(parseJson(text))
  if (!parsed.success)
    throw new ProtocolError("/threads/uploads response is malformed")
  return parsed.data.id
}
