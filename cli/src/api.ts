import {
  isRecord,
  parseJson,
  recordAt,
  stringAt,
  type JsonObject,
} from "./json.ts"
import type { Credential } from "./credentials.ts"
import * as z from "zod"

const identitySchema = z.object({
  login: z.string(),
  email: z.string().nullish(),
  is_admin: z.boolean().nullish(),
})

export type Identity = z.infer<typeof identitySchema>

const workerSchema = z.object({
  worker_thread_id: z.string(),
  thread_url: z.string().nullable(),
  instructions: z.string(),
  state: z.string(),
  run_status: z.string().nullish(),
  error: z.string().nullish(),
  active: z.boolean(),
})

const activitySchema = z.object({
  runs: z.array(z.string()),
  settled: z.boolean(),
  workers: z.array(workerSchema),
})

const stateSchema = z.object({
  interrupts: z.array(z.unknown()).nullish(),
  tasks: z
    .array(z.object({ interrupts: z.array(z.unknown()).nullish() }))
    .nullish(),
})

const runSchema = z.object({ status: z.string() })

export type WorkerActivity = z.infer<typeof workerSchema>

/**
 * What a thread's coordinator is still doing: its runs created after the one
 * the CLI started, oldest first, and the workers it launched.
 */
export type TaskActivity = z.infer<typeof activitySchema>

/** One thread the CLI follows, as its coordinator and that coordinator's workers. */
export interface CoordinatorThread {
  activity(after: string | null): Promise<TaskActivity>
  /** Paused on `interrupt()`: its run ended as `success`, and only the state shows the pause. */
  waiting(): Promise<boolean>
  /** The status of one of the coordinator's runs, as its runtime reports it, such as `success` or `error`. */
  runStatus(runId: string): Promise<string>
  /** Cancels the workers still running, then the coordinator's own runs. */
  stop(): Promise<void>
}

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

  private async headers(): Promise<Record<string, string>> {
    return {
      ...(await this.credential.headers(this.backend)),
      "Content-Type": "application/json",
      Accept: "application/json",
    }
  }

  private async send(
    method: string,
    path: string,
    options: { body?: unknown; raw?: RawBody; signal?: AbortSignal } = {}
  ): Promise<Response> {
    const headers = {
      ...(await this.headers()),
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

  /** Whether the server accepts this credential, for credentials that have no `/me` session. */
  async verifyMachine(): Promise<void> {
    await this.send("GET", "/threads?limit=1")
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
            input: {
              messages: [{ type: "human", content: prompt }],
            },
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

  async taskActivity(
    threadId: string,
    after: string | null
  ): Promise<TaskActivity> {
    const query = after === null ? "" : `?after=${encodeURIComponent(after)}`
    const parsed = activitySchema.safeParse(
      await this.json(
        "GET",
        `/threads/${encodeURIComponent(threadId)}/workers${query}`
      )
    )
    if (!parsed.success)
      throw new ProtocolError("worker activity response is malformed")
    return parsed.data
  }

  async threadWaiting(threadId: string): Promise<boolean> {
    const parsed = stateSchema.safeParse(
      await this.json("GET", `/threads/${encodeURIComponent(threadId)}/state`)
    )
    if (!parsed.success)
      throw new ProtocolError("thread state response is malformed")
    const { interrupts, tasks } = parsed.data
    return (
      (interrupts?.length ?? 0) > 0 ||
      (tasks ?? []).some((task) => (task.interrupts?.length ?? 0) > 0)
    )
  }

  async runStatus(threadId: string, runId: string): Promise<string> {
    const parsed = runSchema.safeParse(
      await this.json(
        "GET",
        `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}`
      )
    )
    if (!parsed.success) throw new ProtocolError("run response is malformed")
    return parsed.data.status
  }

  async cancelWorkers(threadId: string): Promise<void> {
    try {
      await this.send(
        "POST",
        `/threads/${encodeURIComponent(threadId)}/workers/cancel`
      )
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 404)) throw cause
    }
  }

  coordinator(threadId: string): CoordinatorThread {
    return {
      activity: (after) => this.taskActivity(threadId, after),
      waiting: () => this.threadWaiting(threadId),
      runStatus: (runId) => this.runStatus(threadId, runId),
      stop: async () => {
        try {
          await this.cancelWorkers(threadId)
        } finally {
          await this.cancelThread(threadId)
        }
      },
    }
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
