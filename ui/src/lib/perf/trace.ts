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

export interface PerfSink {
  onSpanEnd(span: PerfSpan): void
}

export interface SpanHandle {
  readonly id: string
  readonly ended: boolean
  /** Record a named step at the current time; a repeated name is ignored. */
  mark(step: string, attributes?: PerfAttributes): void
  has(step: string): boolean
  set(attributes: PerfAttributes): void
  /** Add to a numeric attribute, starting from zero. */
  add(attribute: string, value: number): void
  /** Milliseconds since the span started. */
  elapsed(): number
  end(attributes?: PerfAttributes): PerfSpan
  abandon(reason: string): PerfSpan
}

export interface StartSpanOptions {
  /** Override the start, in `performance.now()` time; `0` is the page's time origin. */
  startedAt?: number
}

const MAX_RETAINED_SPANS = 60
const PERF_FLAG_STORAGE_KEY = "open-swe.perf"

const spans: Array<PerfSpan> = []
const sinks = new Set<PerfSink>()
const listeners = new Set<() => void>()
let nextSpanId = 0

function hasPerformance(): boolean {
  return (
    typeof performance !== "undefined" && typeof performance.now === "function"
  )
}

export function perfNow(): number {
  return hasPerformance() ? performance.now() : Date.now()
}

function timeOriginEpochMs(): number {
  return hasPerformance() && typeof performance.timeOrigin === "number"
    ? performance.timeOrigin
    : Date.now() - perfNow()
}

function userTimingMark(name: string, startTime: number): void {
  if (!hasPerformance() || typeof performance.mark !== "function") return
  try {
    performance.mark(name, { startTime })
  } catch {
    // Older engines only take the name; the mark is a convenience anyway.
    try {
      performance.mark(name)
    } catch {}
  }
}

function userTimingMeasure(span: PerfSpan): void {
  if (
    !hasPerformance() ||
    typeof performance.measure !== "function" ||
    span.duration === null
  )
    return
  try {
    performance.measure(`osw:${span.name}`, {
      start: span.startedAt,
      end: span.startedAt + span.duration,
      detail: { steps: span.steps, attributes: span.attributes },
    })
  } catch {}
}

function retain(span: PerfSpan): void {
  spans.push(span)
  if (spans.length > MAX_RETAINED_SPANS) spans.shift()
  notify()
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

export function setPerfHudEnabled(enabled: boolean): void {
  if (typeof window === "undefined") return
  try {
    if (enabled) window.localStorage.setItem(PERF_FLAG_STORAGE_KEY, "1")
    else window.localStorage.removeItem(PERF_FLAG_STORAGE_KEY)
  } catch {}
  notify()
}

function isConsoleEnabled(): boolean {
  return import.meta.env.DEV || isPerfHudEnabled()
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
  if (isConsoleEnabled()) {
    console.debug(`[perf] ${formatSpan(span)}`, span.attributes)
  }
  notify()
  if (status !== "ended") return span
  for (const sink of sinks) {
    try {
      sink.onSpanEnd(span)
    } catch {
      // A failing sink must never affect the app.
    }
  }
  return span
}

export function startSpan(
  name: PerfSpanName,
  attributes: PerfAttributes = {},
  options: StartSpanOptions = {}
): SpanHandle {
  const startedAt = options.startedAt ?? perfNow()
  const span: PerfSpan = {
    id: `${name}-${++nextSpanId}`,
    name,
    startedAt,
    startEpochMs: timeOriginEpochMs() + startedAt,
    steps: [],
    attributes: { ...attributes },
    status: "open",
    duration: null,
  }
  userTimingMark(`osw:${name}:start`, startedAt)
  retain(span)

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
    elapsed() {
      return perfNow() - startedAt
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
    {
      exportedAt: new Date().toISOString(),
      userAgent: typeof navigator === "undefined" ? null : navigator.userAgent,
      spans,
    },
    null,
    2
  )
}

interface PerfGlobal {
  spans: () => ReadonlyArray<PerfSpan>
  export: () => string
  clear: () => void
  hud: (enabled: boolean) => void
}

/** `window.__openSwePerf` for poking at spans from the console or Playwright. */
export function exposePerfGlobal(): void {
  if (typeof window === "undefined") return
  const target = window as Window & { __openSwePerf?: PerfGlobal }
  target.__openSwePerf ??= {
    spans: getPerfSpans,
    export: exportPerfSpans,
    clear: clearPerfSpans,
    hud: setPerfHudEnabled,
  }
}
