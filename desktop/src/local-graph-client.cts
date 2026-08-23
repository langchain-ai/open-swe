import { modelCredentialStatus, type CredentialStatus } from "./local-runtime.cjs"

const THREAD_STATUS = { busy: "running", error: "error" } as const

interface LocalGraphClientOptions {
  /** Origin of the combined local server, or null before it is up. */
  origin: () => string | null
  env?: () => NodeJS.ProcessEnv
  openAiOAuthAvailable?: () => boolean
  fetch?: typeof fetch
}

/**
 * Talks to the graph through the local server's `/local-graph` route rather
 * than to the graph's own port, which is private to that process. Requests
 * from the main process carry no `Origin`, which the route's same-origin gate
 * admits, and the route attaches the graph's bearer token.
 */
export class LocalGraphClient {
  private readonly options: LocalGraphClientOptions
  private readonly fetch: typeof fetch

  constructor(options: LocalGraphClientOptions) {
    this.options = options
    this.fetch = options.fetch || fetch
  }

  credentialStatus(modelId: unknown): CredentialStatus {
    return modelCredentialStatus(
      modelId,
      { ...process.env, ...(this.options.env?.() ?? {}) },
      { openAiOAuth: this.options.openAiOAuthAvailable?.() === true }
    )
  }

  async request(
    pathname: string,
    init: RequestInit & { duplex?: "half" } = {}
  ): Promise<Response> {
    const origin = this.options.origin()
    if (!origin) throw new Error("Local server is not running")
    const headers = new Headers(init.headers)
    headers.set("accept-encoding", "identity")
    return this.fetch(`${origin}/local-graph${pathname}`, { ...init, headers })
  }

  async threadActivity(): Promise<Record<string, "running" | "error"> | null> {
    if (!this.options.origin()) return {}
    try {
      const response = await this.request("/threads/search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ limit: 1_000 }),
        signal: AbortSignal.timeout(2_000),
      })
      if (!response.ok) return null
      const threads: unknown = await response.json()
      if (!Array.isArray(threads)) return null
      const activity: Record<string, "running" | "error"> = {}
      for (const thread of threads) {
        if (!thread || typeof thread !== "object") continue
        const value = thread as {
          thread_id?: unknown
          status?: keyof typeof THREAD_STATUS
        }
        const status = value.status ? THREAD_STATUS[value.status] : undefined
        if (status && typeof value.thread_id === "string") {
          activity[value.thread_id] = status
        }
      }
      return activity
    } catch {
      return null
    }
  }

  async createThread(threadId: string): Promise<void> {
    const response = await this.request("/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        if_exists: "do_nothing",
        metadata: { graph_id: "agent" },
      }),
    })
    if (!response.ok) {
      throw new Error(`Could not create local graph thread (${response.status})`)
    }
  }

  async deleteThread(threadId: string): Promise<void> {
    const response = await this.request(
      `/threads/${encodeURIComponent(threadId)}`,
      { method: "DELETE" }
    )
    if (!response.ok && response.status !== 404) {
      throw new Error(`Could not delete local graph thread (${response.status})`)
    }
  }
}
