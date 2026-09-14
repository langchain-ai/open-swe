/**
 * The `agent_run` span: one run as the browser experiences it.
 *
 *   submit ─▶ accepted ─▶ stream_open ─▶ first_event ─▶ first_token ─▶ end
 *
 * `first_token` is the client-side time to first assistant text, the
 * counterpart of the backend's `open_swe_dashboard_thread_ttft` histogram. The
 * span also counts protocol events and text deltas, samples the lag between the
 * server's event timestamp and receipt (clock skew included, so read it as a
 * trend rather than an absolute), and, in development builds, accumulates React
 * commit time for the transcript while the run streams.
 */

import { subscribeRequestTimings } from "./fetchTiming"
import type { RequestTiming } from "./fetchTiming"
import { startSpan } from "./trace"
import type { PerfAttributes, SpanHandle } from "./trace"

export type RunTransport = "cloud" | "local"
export type RunEndReason = "success" | "error" | "interrupt" | "stopped"

export interface RunTracker {
  /** The user pressed send on this stream. */
  submitted(): void
  /** The server accepted the run. */
  created(): void
  /** A raw protocol event from the `lifecycle` or `messages` channel. */
  event(event: unknown): void
  /** The run's streaming phase ended. */
  completed(reason: RunEndReason): void
  dispose(): void
}

interface RunState {
  span: SpanHandle
  events: number
  textDeltas: number
  lagSum: number
  lagMax: number
  lagSamples: number
  builds: number
  buildMs: number
  commits: number
  commitMs: number
  commitMaxMs: number
}

const openRuns = new Set<RunTrackerImpl>()
let requestTimingsSubscribed = false

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isRootNamespace(params: Record<string, unknown>): boolean {
  const namespace = params["namespace"]
  return Array.isArray(namespace) && namespace.length === 0
}

function subscribeRequestTimingsOnce(): void {
  if (requestTimingsSubscribed) return
  requestTimingsSubscribed = true
  subscribeRequestTimings(onRequestTiming)
}

function onRequestTiming(timing: RequestTiming): void {
  if (timing.kind === "command") {
    for (const run of openRuns) {
      run.state?.span.set({ command_ttfb_ms: Math.round(timing.ttfbMs) })
    }
    return
  }
  if (timing.kind !== "stream_events") return
  for (const run of openRuns) {
    run.state?.span.mark("stream_open", {
      stream_ttfb_ms: Math.round(timing.ttfbMs),
    })
  }
}

class RunTrackerImpl implements RunTracker {
  state: RunState | null = null

  constructor(private readonly attributes: PerfAttributes) {}

  private begin(extra: PerfAttributes): void {
    if (this.state && !this.state.span.ended) {
      this.state.span.abandon("superseded")
    }
    subscribeRequestTimingsOnce()
    this.state = {
      span: startSpan("agent_run", { ...this.attributes, ...extra }),
      events: 0,
      textDeltas: 0,
      lagSum: 0,
      lagMax: 0,
      lagSamples: 0,
      builds: 0,
      buildMs: 0,
      commits: 0,
      commitMs: 0,
      commitMaxMs: 0,
    }
    openRuns.add(this)
  }

  submitted(): void {
    this.begin({ joined: false })
  }

  created(): void {
    this.state?.span.mark("accepted")
  }

  event(event: unknown): void {
    if (!isRecord(event) || !isRecord(event["params"])) return
    const params = event["params"]
    const data = params["data"]
    if (!isRecord(data)) return
    const method = event["method"]

    if (
      method === "lifecycle" &&
      isRootNamespace(params) &&
      data["event"] === "running"
    ) {
      // A run this client did not submit (queued message, another tab, Slack).
      if (!this.state || this.state.span.ended) this.begin({ joined: true })
      this.state?.span.mark("accepted")
    }

    const state = this.state
    if (!state || state.span.ended) return

    state.events += 1
    state.span.mark("first_event")

    const timestamp = params["timestamp"]
    if (typeof timestamp === "number" && Number.isFinite(timestamp)) {
      const lag = Date.now() - timestamp
      state.lagSum += lag
      state.lagMax = Math.max(state.lagMax, lag)
      state.lagSamples += 1
    }

    if (method !== "messages" || data["event"] !== "content-block-delta") return
    const delta = data["delta"]
    if (!isRecord(delta) || delta["type"] !== "text-delta") return
    const text = delta["text"]
    if (typeof text !== "string" || !text) return
    state.textDeltas += 1
    state.span.mark("first_token")
  }

  transcriptBuilt(durationMs: number): void {
    const state = this.state
    if (!state || state.span.ended) return
    state.builds += 1
    state.buildMs += durationMs
  }

  transcriptCommitted(durationMs: number): void {
    const state = this.state
    if (!state || state.span.ended) return
    state.commits += 1
    state.commitMs += durationMs
    state.commitMaxMs = Math.max(state.commitMaxMs, durationMs)
  }

  completed(reason: RunEndReason): void {
    const state = this.state
    if (!state || state.span.ended) return
    const attributes: PerfAttributes = {
      reason,
      events: state.events,
      text_deltas: state.textDeltas,
      builds: state.builds,
      build_ms: Math.round(state.buildMs),
    }
    if (state.lagSamples > 0) {
      attributes["lag_avg_ms"] = Math.round(state.lagSum / state.lagSamples)
      attributes["lag_max_ms"] = Math.round(state.lagMax)
    }
    if (state.commits > 0) {
      attributes["commits"] = state.commits
      attributes["commit_ms"] = Math.round(state.commitMs)
      attributes["commit_max_ms"] = Math.round(state.commitMaxMs)
    }
    state.span.end(attributes)
    openRuns.delete(this)
    this.state = null
  }

  dispose(): void {
    if (this.state && !this.state.span.ended) {
      this.state.span.abandon("unmounted")
    }
    openRuns.delete(this)
    this.state = null
  }
}

export function createRunTracker(attributes: {
  transport: RunTransport
}): RunTracker {
  return new RunTrackerImpl({ transport: attributes.transport })
}

/**
 * Transcript work while a run streams. There is normally one visible run, so
 * every open run is charged; a background run in the pool inflates its own
 * numbers only while another thread is on screen.
 */
export function runTranscriptBuilt(durationMs: number): void {
  for (const run of openRuns) run.transcriptBuilt(durationMs)
}

/** React `Profiler` commit durations; only fires in development builds. */
export function runTranscriptCommitted(durationMs: number): void {
  for (const run of openRuns) run.transcriptCommitted(durationMs)
}

export function hasOpenRuns(): boolean {
  return openRuns.size > 0
}
