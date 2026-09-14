/**
 * Client-side performance spans for the dashboard.
 *
 * A span is one user-visible operation (opening a thread, one agent run) with
 * named steps along the way. Every span is mirrored into the User Timing API
 * (`performance.mark` / `performance.measure`) so it shows up in the Chrome
 * Performance panel and Playwright traces for free, kept in a small ring buffer
 * for the local HUD and `window.__openSwePerf`, and handed to registered sinks
 * (Datadog RUM in production) when it ends.
 */

export type PerfAttributeValue = string | number | boolean | null
export type PerfAttributes = Record<string, PerfAttributeValue>
export type PerfSpanName = "thread_load" | "agent_run"
export type PerfSpanStatus = "open" | "ended" | "abandoned"

export interface PerfStep {
  name: string
  /** Milliseconds since the span started. */
  at: number
}

export interface PerfSpan {
  id: string
  name: PerfSpanName
  /** `performance.now()` at span start. */
  startedAt: number
  /** Wall clock (epoch ms) at span start, which is what RUM vitals expect. */
  startEpochMs: number
  steps: Array<PerfStep>
  attributes: PerfAttributes
  status: PerfSpanStatus
  duration: number | null
}

export type PerfSink = (span: PerfSpan) => void

export interface SpanHandle {
  readonly id: string
  readonly ended: boolean
  /** Record a named step at the current time; a repeated name is ignored. */
  mark(step: string, attributes?: PerfAttributes): void
  has(step: string): boolean
  set(attributes: PerfAttributes): void
  /** Add to a numeric attribute, starting from zero. */
  add(attribute: string, value: number): void
  end(attributes?: PerfAttributes): PerfSpan
  abandon(reason: string): PerfSpan
}

const MAX_RETAINED_SPANS = 60
const PERF_FLAG_STORAGE_KEY = "open-swe.perf"

const spans: Array<PerfSpan> = []
const sinks = new Set<PerfSink>()
const listeners = new Set<() => void>()
let nextSpanId = 0

export function perfNow(): number {
  return performance.now()
}

function userTimingMark(name: string, startTime: number): void {
  try {
    performance.mark(name, { startTime })
  } catch {}
}

function userTimingMeasure(span: PerfSpan): void {
  if (span.duration === null) return
  try {
    performance.measure(`osw:${span.name}`, {
      start: span.startedAt,
      end: span.startedAt + span.duration,
      detail: { steps: span.steps, attributes: span.attributes },
    })
  } catch {}
}

function notify(): void {
  listeners.forEach((listener) => listener())
}

/** Query flag wins and is persisted, so `?perf=1` sticks and `?perf=0` clears. */
export function isPerfHudEnabled(): boolean {
  if (typeof window === "undefined") return false
  try {
    const requested = new URLSearchParams(window.location.search).get("perf")
    if (requested === "1")
      window.localStorage.setItem(PERF_FLAG_STORAGE_KEY, "1")
    else if (requested === "0")
      window.localStorage.removeItem(PERF_FLAG_STORAGE_KEY)
    return window.localStorage.getItem(PERF_FLAG_STORAGE_KEY) === "1"
  } catch {
    return false
  }
}

export function formatSpan(span: PerfSpan): string {
  const steps = span.steps
    .map((step) => `${step.name} ${Math.round(step.at)}`)
    .join(" · ")
  const duration =
    span.duration === null ? "open" : `${Math.round(span.duration)}ms`
  const status = span.status === "abandoned" ? " (abandoned)" : ""
  return `${span.name} ${duration}${status}${steps ? ` — ${steps}` : ""}`
}

function finish(
  span: PerfSpan,
  status: Exclude<PerfSpanStatus, "open">,
  attributes?: PerfAttributes
): PerfSpan {
  if (span.status !== "open") return span
  span.status = status
  span.duration = perfNow() - span.startedAt
  if (attributes) Object.assign(span.attributes, attributes)
  if (status === "ended") {
    userTimingMark(`osw:${span.name}:end`, span.startedAt + span.duration)
    userTimingMeasure(span)
  }
  if (import.meta.env.DEV) {
    console.debug(`[perf] ${formatSpan(span)}`, span.attributes)
  }
  notify()
  if (status !== "ended") return span
  for (const sink of sinks) {
    try {
      sink(span)
    } catch {
      // A failing sink must never affect the app.
    }
  }
  return span
}

export function startSpan(
  name: PerfSpanName,
  attributes: PerfAttributes = {},
  /** Override the start, in `performance.now()` time; `0` is the page's time origin. */
  options: { startedAt?: number } = {}
): SpanHandle {
  const startedAt = options.startedAt ?? perfNow()
  const span: PerfSpan = {
    id: `${name}-${++nextSpanId}`,
    name,
    startedAt,
    startEpochMs: performance.timeOrigin + startedAt,
    steps: [],
    attributes: { ...attributes },
    status: "open",
    duration: null,
  }
  userTimingMark(`osw:${name}:start`, startedAt)
  spans.push(span)
  if (spans.length > MAX_RETAINED_SPANS) spans.shift()
  notify()

  return {
    id: span.id,
    get ended() {
      return span.status !== "open"
    },
    mark(step, stepAttributes) {
      if (span.status !== "open" || span.steps.some((s) => s.name === step))
        return
      const at = perfNow() - startedAt
      span.steps.push({ name: step, at })
      if (stepAttributes) Object.assign(span.attributes, stepAttributes)
      userTimingMark(`osw:${name}:${step}`, startedAt + at)
      notify()
    },
    has(step) {
      return span.steps.some((s) => s.name === step)
    },
    set(next) {
      if (span.status !== "open") return
      Object.assign(span.attributes, next)
    },
    add(attribute, value) {
      if (span.status !== "open") return
      const current = span.attributes[attribute]
      span.attributes[attribute] =
        (typeof current === "number" ? current : 0) + value
    },
    end(endAttributes) {
      return finish(span, "ended", endAttributes)
    },
    abandon(reason) {
      return finish(span, "abandoned", { abandoned_reason: reason })
    },
  }
}

export function registerPerfSink(sink: PerfSink): () => void {
  sinks.add(sink)
  return () => sinks.delete(sink)
}

export function getPerfSpans(): ReadonlyArray<PerfSpan> {
  return spans
}

export function clearPerfSpans(): void {
  spans.splice(0, spans.length)
  notify()
}

export function subscribePerfSpans(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function exportPerfSpans(): string {
  return JSON.stringify(
    { exportedAt: new Date().toISOString(), userAgent: navigator.userAgent, spans },
    null,
    2
  )
}

/** `window.__openSwePerf` for poking at spans from the console or Playwright. */
export function exposePerfGlobal(): void {
  const target = window as Window & {
    __openSwePerf?: {
      spans: () => ReadonlyArray<PerfSpan>
      export: () => string
      clear: () => void
    }
  }
  target.__openSwePerf ??= {
    spans: getPerfSpans,
    export: exportPerfSpans,
    clear: clearPerfSpans,
  }
}
