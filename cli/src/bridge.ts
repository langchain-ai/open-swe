import { hostname } from "node:os"

import {
  ApiError,
  type ApiClient,
  type BridgeRequest,
  type BridgeSession,
  type CreateBridgeInput,
} from "./api.ts"
import { LocalExecutor, type DispatchOutcome } from "./executor.ts"
import { errorMessage } from "./json.ts"

const POLL_WAIT_SECONDS = 25
const POLL_LIMIT = 8
const MIN_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 10_000
const REPLY_ATTEMPTS = 3
/** Margin over the server's long-poll window before the request is abandoned. */
const POLL_TIMEOUT_MS = (POLL_WAIT_SECONDS + 15) * 1_000

export class CredentialRejectedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "CredentialRejectedError"
  }
}

export class BridgeGoneError extends Error {
  constructor(bridgeId: string) {
    super(`bridge ${bridgeId} no longer exists on the server`)
    this.name = "BridgeGoneError"
  }
}

export interface OpenBridgeOptions {
  rootPath: string
  label: string | null
  rememberedBridgeId: string | null
}

function sleep(ms: number): Promise<void> {
  return new Promise((done) => setTimeout(done, ms))
}

/**
 * The CLI half of a sandbox bridge: long-polls the backend for the remote
 * agent's requests, runs them in the local checkout, and posts the results.
 */
export class Bridge {
  private running = false
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private readonly inFlight = new Set<Promise<void>>()
  private readonly pollControllers = new Set<AbortController>()
  private onFatal: ((error: Error) => void) | null = null
  private readonly executor: LocalExecutor

  private constructor(
    private readonly api: ApiClient,
    readonly session: BridgeSession,
    private readonly input: Omit<CreateBridgeInput, "bridgeId">,
    readonly reopened: boolean
  ) {
    this.executor = new LocalExecutor(input.rootPath)
  }

  get rootPath(): string {
    return this.input.rootPath
  }

  static async open(
    api: ApiClient,
    options: OpenBridgeOptions
  ): Promise<Bridge> {
    const input = {
      rootPath: options.rootPath,
      hostname: hostname(),
      label: options.label,
    }
    if (options.rememberedBridgeId !== null) {
      try {
        const session = await api.createBridge({
          ...input,
          bridgeId: options.rememberedBridgeId,
        })
        return new Bridge(api, session, input, true)
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause
      }
    }
    const session = await api.createBridge({ ...input, bridgeId: null })
    return new Bridge(api, session, input, false)
  }

  start(onFatal: (error: Error) => void): void {
    if (this.running) return
    this.running = true
    this.onFatal = onFatal
    const interval = Math.max(this.session.heartbeatIntervalSeconds, 1) * 1_000
    this.heartbeatTimer = setInterval(() => void this.beat(), interval)
    void this.poll()
  }

  async close(): Promise<void> {
    this.running = false
    if (this.heartbeatTimer !== null) clearInterval(this.heartbeatTimer)
    this.heartbeatTimer = null
    for (const controller of this.pollControllers) controller.abort()
    this.pollControllers.clear()
    await Promise.allSettled(this.inFlight)
    try {
      await this.api.deleteBridge(this.session.bridgeId)
    } catch (cause) {
      if (!(cause instanceof ApiError) || cause.status !== 404) {
        process.stderr.write(
          `oswe: could not release the bridge: ${errorMessage(cause)}\n`
        )
      }
    }
  }

  private fail(error: Error): void {
    if (!this.running) return
    this.running = false
    this.onFatal?.(error)
  }

  /** The server closes a bridge whose heartbeats stopped (the machine slept); reopen it in place. */
  private async reopen(): Promise<void> {
    try {
      await this.api.createBridge({
        ...this.input,
        bridgeId: this.session.bridgeId,
      })
      process.stderr.write("oswe: bridge reconnected\n")
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        this.fail(new CredentialRejectedError(this.api.credential.rejected))
        return
      }
      if (cause instanceof ApiError && cause.status === 404) {
        this.fail(new BridgeGoneError(this.session.bridgeId))
        return
      }
      process.stderr.write(
        `oswe: could not reopen the bridge: ${errorMessage(cause)}\n`
      )
    }
  }

  private async beat(): Promise<void> {
    if (!this.running) return
    try {
      await this.api.heartbeat(this.session.bridgeId)
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        this.fail(new CredentialRejectedError(this.api.credential.rejected))
        return
      }
      if (cause instanceof ApiError && cause.status === 404) {
        this.fail(new BridgeGoneError(this.session.bridgeId))
        return
      }
      if (cause instanceof ApiError && cause.status === 409) {
        await this.reopen()
        return
      }
      process.stderr.write(`oswe: heartbeat failed: ${errorMessage(cause)}\n`)
    }
  }

  private async poll(): Promise<void> {
    let backoff = MIN_BACKOFF_MS
    while (this.running) {
      const controller = new AbortController()
      const deadline = setTimeout(() => controller.abort(), POLL_TIMEOUT_MS)
      this.pollControllers.add(controller)
      try {
        const requests = await this.api.pollRequests(this.session.bridgeId, {
          wait: POLL_WAIT_SECONDS,
          limit: POLL_LIMIT,
          signal: controller.signal,
        })
        backoff = MIN_BACKOFF_MS
        for (const request of requests) this.track(this.serve(request))
      } catch (cause) {
        if (!this.running) return
        if (cause instanceof ApiError && cause.status === 401) {
          this.fail(new CredentialRejectedError(this.api.credential.rejected))
          return
        }
        if (cause instanceof ApiError && cause.status === 404) {
          this.fail(new BridgeGoneError(this.session.bridgeId))
          return
        }
        if (cause instanceof ApiError && cause.status === 409) {
          await this.reopen()
          await sleep(backoff)
          backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
          continue
        }
        if (
          cause instanceof ApiError &&
          cause.status < 500 &&
          cause.status !== 408 &&
          cause.status !== 429
        ) {
          this.fail(cause)
          return
        }
        await sleep(backoff)
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
      } finally {
        clearTimeout(deadline)
        this.pollControllers.delete(controller)
      }
    }
  }

  private track(work: Promise<void>): void {
    this.inFlight.add(work)
    void work.finally(() => this.inFlight.delete(work))
  }

  private async serve(request: BridgeRequest): Promise<void> {
    let reply: DispatchOutcome
    try {
      reply = await this.executor.dispatch(request.method, request.params)
    } catch (cause) {
      reply = { error: errorMessage(cause) }
    }
    for (let attempt = 1; attempt <= REPLY_ATTEMPTS; attempt += 1) {
      try {
        await this.api.respond(this.session.bridgeId, request.requestId, reply)
        return
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 401) {
          this.fail(new CredentialRejectedError(this.api.credential.rejected))
          return
        }
        if (cause instanceof ApiError && cause.status === 404) return
        if (attempt === REPLY_ATTEMPTS || !this.running) {
          process.stderr.write(
            `oswe: dropped the result for ${request.method}: ${errorMessage(cause)}\n`
          )
          return
        }
        await sleep(MIN_BACKOFF_MS * attempt)
      }
    }
  }
}
