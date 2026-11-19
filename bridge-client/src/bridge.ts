import { hostname } from "node:os"

import {
  httpStatus,
  type BridgeApi,
  type BridgeClient,
  type BridgeReply,
  type BridgeRequest,
  type BridgeSession,
} from "./api"
import { errorMessage, type JsonObject } from "./json"

const POLL_WAIT_SECONDS = 25
const POLL_LIMIT = 8
const MIN_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 10_000
const REPLY_ATTEMPTS = 3
/**
 * Below the server's cap of 1024. An id this far back is long settled, so
 * leaving it out of a poll cannot get it served again.
 */
const MAX_HELD_SENT = 1_000
/** How long `close` waits for requests in flight to report before releasing the bridge. */
const CLOSE_GRACE_MS = 5_000
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

export interface BridgeOptions {
  client: BridgeClient
  /** What the bridge serves, which the backend records with it. */
  rootPath: string
  label: string | null
  /** A bridge to reopen in place; null opens a new one. */
  bridgeId: string | null
  /** What to tell the user when the backend stops accepting the credential. */
  credentialRejected: string
  /** Where non-fatal trouble is reported; the loop itself never throws. */
  log: (message: string) => void
  /** The methods the host app serves; any other request is answered with an error. */
  handlers: Record<string, BridgeHandler>
}

export type BridgeHandler = (
  params: Record<string, unknown>
) => Promise<JsonObject>

function sleep(ms: number): Promise<void> {
  return new Promise((done) => setTimeout(done, ms))
}

/**
 * The machine half of a bridge: long-polls the backend for a cloud run's
 * requests, answers each with the host app's handler for its method, and posts
 * the results.
 */
export class Bridge {
  private active = false
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private readonly inFlight = new Set<Promise<void>>()
  private readonly held = new Set<string>()
  private readonly pollControllers = new Set<AbortController>()
  private onFatal: ((error: Error) => void) | null = null

  private constructor(
    private readonly api: BridgeApi,
    readonly session: BridgeSession,
    private readonly options: BridgeOptions
  ) {}

  get running(): boolean {
    return this.active
  }

  static async open(api: BridgeApi, options: BridgeOptions): Promise<Bridge> {
    const session = await api.createBridge(Bridge.registration(options))
    return new Bridge(api, session, options)
  }

  private static registration(options: BridgeOptions) {
    return {
      client: options.client,
      rootPath: options.rootPath,
      hostname: hostname(),
      label: options.label,
      bridgeId: options.bridgeId,
    }
  }

  start(onFatal: (error: Error) => void): void {
    if (this.active) return
    this.active = true
    this.onFatal = onFatal
    const interval = Math.max(this.session.heartbeatIntervalSeconds, 1) * 1_000
    this.heartbeatTimer = setInterval(() => void this.beat(), interval)
    void this.poll()
  }

  async close(): Promise<void> {
    this.active = false
    if (this.heartbeatTimer !== null) clearInterval(this.heartbeatTimer)
    this.heartbeatTimer = null
    for (const controller of this.pollControllers) controller.abort()
    this.pollControllers.clear()
    // Closing is quitting: a reply that cannot be posted is not waited on past
    // the grace period.
    let grace: ReturnType<typeof setTimeout> | undefined
    await Promise.race([
      Promise.allSettled(this.inFlight),
      new Promise<void>((done) => {
        grace = setTimeout(done, CLOSE_GRACE_MS)
      }),
    ])
    clearTimeout(grace)
    try {
      await this.api.deleteBridge(this.session.bridgeId)
    } catch (cause) {
      if (httpStatus(cause) !== 404) {
        this.options.log(`could not release the bridge: ${errorMessage(cause)}`)
      }
    }
  }

  private fail(error: Error): void {
    if (!this.active) return
    this.active = false
    this.onFatal?.(error)
  }

  /** The server closes a bridge whose heartbeats stopped (the machine slept); reopen it in place. */
  private async reopen(): Promise<void> {
    try {
      await this.api.createBridge({
        ...Bridge.registration(this.options),
        bridgeId: this.session.bridgeId,
      })
      this.options.log("bridge reconnected")
    } catch (cause) {
      if (httpStatus(cause) === 401) {
        this.fail(new CredentialRejectedError(this.options.credentialRejected))
        return
      }
      if (httpStatus(cause) === 404) {
        this.fail(new BridgeGoneError(this.session.bridgeId))
        return
      }
      this.options.log(`could not reopen the bridge: ${errorMessage(cause)}`)
    }
  }

  private async beat(): Promise<void> {
    if (!this.active) return
    try {
      await this.api.heartbeat(this.session.bridgeId)
    } catch (cause) {
      if (httpStatus(cause) === 401) {
        this.fail(new CredentialRejectedError(this.options.credentialRejected))
        return
      }
      if (httpStatus(cause) === 404) {
        this.fail(new BridgeGoneError(this.session.bridgeId))
        return
      }
      if (httpStatus(cause) === 409) {
        await this.reopen()
        return
      }
      this.options.log(`heartbeat failed: ${errorMessage(cause)}`)
    }
  }

  private async poll(): Promise<void> {
    let backoff = MIN_BACKOFF_MS
    while (this.active) {
      const controller = new AbortController()
      const deadline = setTimeout(() => controller.abort(), POLL_TIMEOUT_MS)
      this.pollControllers.add(controller)
      try {
        const requests = await this.api.pollRequests(this.session.bridgeId, {
          wait: POLL_WAIT_SECONDS,
          limit: POLL_LIMIT,
          held: [...this.held].slice(-MAX_HELD_SENT),
          signal: controller.signal,
        })
        backoff = MIN_BACKOFF_MS
        for (const request of requests) this.track(this.serve(request))
      } catch (cause) {
        if (!this.active) return
        if (httpStatus(cause) === 401) {
          this.fail(
            new CredentialRejectedError(this.options.credentialRejected)
          )
          return
        }
        if (httpStatus(cause) === 404) {
          this.fail(new BridgeGoneError(this.session.bridgeId))
          return
        }
        if (httpStatus(cause) === 409) {
          await this.reopen()
          await sleep(backoff)
          backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
          continue
        }
        const status = httpStatus(cause)
        if (
          status !== null &&
          status < 500 &&
          status !== 408 &&
          status !== 429
        ) {
          this.fail(cause instanceof Error ? cause : new Error(String(cause)))
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
    // Held until the server has the answer. A result that could not be
    // delivered stays held, so the request is never served a second time.
    this.held.add(request.requestId)
    let reply: BridgeReply
    try {
      const handler = this.options.handlers[request.method]
      reply = handler
        ? { result: await handler(request.params) }
        : { error: `unsupported method ${request.method}` }
    } catch (cause) {
      reply = { error: errorMessage(cause) }
    }
    for (let attempt = 1; attempt <= REPLY_ATTEMPTS; attempt += 1) {
      try {
        await this.api.respond(this.session.bridgeId, request.requestId, reply)
        this.held.delete(request.requestId)
        return
      } catch (cause) {
        if (httpStatus(cause) === 401) {
          this.fail(
            new CredentialRejectedError(this.options.credentialRejected)
          )
          return
        }
        // 404: the request is gone. 409: it is settled already, by an earlier
        // attempt whose response was lost, the agent's own timeout, or the
        // bridge closing. Either way the server keeps no claim to re-offer.
        const status = httpStatus(cause)
        if (status === 404 || status === 409) {
          this.held.delete(request.requestId)
          return
        }
        if (attempt === REPLY_ATTEMPTS || !this.active) {
          this.options.log(
            `dropped the result for ${request.method}: ${errorMessage(cause)}`
          )
          return
        }
        await sleep(MIN_BACKOFF_MS * attempt)
      }
    }
  }
}
