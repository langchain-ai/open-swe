/**
 * The `thread_load` span: from the moment the user asks for a thread until its
 * transcript is on screen.
 *
 *   navigate ─▶ detail | local ─▶ hydrate ─▶ paint
 *
 * `detail` is the dashboard's thread summary (`GET /threads/:id`); for a
 * desktop thread under `/agents/local/:id` the equivalent step is `local`, the
 * desktop's `getLocalThread` IPC. `hydrate` is the SDK's state fetch
 * (`GET /threads/:id/state`) that seeds the transcript, and `paint` the first
 * frame after the transcript rendered. A full page load starts the span at the
 * document's time origin so it includes bundle boot.
 */

import { subscribeRequestTimings } from "./fetchTiming"
import type { RequestTiming } from "./fetchTiming"
import { startSpan } from "./trace"
import type { PerfAttributes, SpanHandle } from "./trace"

const THREAD_PATH_RE =
  /^\/(agents(?:\/local)?|assistant)\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i

/** Which thread UI rendered the transcript, so the two can be compared on the same span. */
export type ThreadLoadUi = "agents" | "assistant"

let active: { threadId: string; span: SpanHandle; buildMs: number } | null =
  null
let navigated = false

function threadFromPath(
  pathname: string
): { threadId: string; ui: ThreadLoadUi } | null {
  const match = THREAD_PATH_RE.exec(pathname)
  if (!match?.[2]) return null
  return {
    threadId: match[2],
    ui: match[1] === "assistant" ? "assistant" : "agents",
  }
}

function current(threadId: string): SpanHandle | null {
  if (!active || active.threadId !== threadId || active.span.ended) return null
  return active.span
}

function abandonThreadLoad(reason: string): void {
  if (active && !active.span.ended) active.span.abandon(reason)
  active = null
}

subscribeRequestTimings((timing: RequestTiming) => {
  if (!active || active.span.ended) return
  if (active.threadId.toLowerCase() !== timing.threadId) return
  if (
    timing.kind !== "thread_detail" &&
    timing.kind !== "thread_state" &&
    timing.kind !== "thread_transcript"
  )
    return
  // The snapshot is the transcript log's hydration request, so it reports under
  // the same `state_*` attributes the SDK's state fetch does.
  const prefix = timing.kind === "thread_detail" ? "detail" : "state"
  const attributes: PerfAttributes = {
    [`${prefix}_ttfb_ms`]: Math.round(timing.ttfbMs),
    [`${prefix}_status`]: timing.status,
  }
  for (const entry of timing.serverTiming) {
    if (entry.duration !== null)
      attributes[`${prefix}_srv_${entry.name}_ms`] = Math.round(entry.duration)
  }
  active.span.set(attributes)
})

function beginThreadLoad(
  threadId: string,
  source: "navigation" | "page_load",
  ui: ThreadLoadUi
): void {
  if (typeof window === "undefined") return
  if (current(threadId)) return
  abandonThreadLoad("superseded")
  active = {
    threadId,
    buildMs: 0,
    span: startSpan(
      "thread_load",
      { source, cold: source === "page_load", ui },
      source === "page_load" ? { startedAt: 0 } : undefined
    ),
  }
}

/** Router hook: a thread route entered by client navigation starts the clock here. */
export function onRouterNavigation(
  toPathname: string,
  fromPathname: string | undefined
): void {
  if (typeof window === "undefined") return
  navigated = true
  const to = threadFromPath(toPathname)
  const from = fromPathname ? threadFromPath(fromPathname) : null
  if (to && to.threadId !== from?.threadId)
    beginThreadLoad(to.threadId, "navigation", to.ui)
  else if (!to && active) abandonThreadLoad("navigated_away")
}

/**
 * Page hook: makes sure a span exists once the thread page mounts. Without a
 * prior client navigation this is a full page load, measured from time origin.
 */
export function ensureThreadLoad(
  threadId: string,
  ui: ThreadLoadUi = "agents"
): void {
  if (current(threadId)) return
  beginThreadLoad(threadId, navigated ? "navigation" : "page_load", ui)
}

export function threadDetailResolved(
  threadId: string,
  attributes: { cached: boolean }
): void {
  current(threadId)?.mark("detail", { detail_cached: attributes.cached })
}

export function threadLocalResolved(threadId: string): void {
  current(threadId)?.mark("local")
}

export function threadDetailFailed(threadId: string): void {
  if (current(threadId)) abandonThreadLoad("detail_failed")
}

export function threadHydrated(threadId: string): void {
  current(threadId)?.mark("hydrate")
}

export function threadHydrationFailed(threadId: string): void {
  if (current(threadId)) abandonThreadLoad("hydration_failed")
}

/** Time spent turning SDK messages into transcript rows before the first paint. */
export function threadTranscriptBuilt(
  threadId: string,
  durationMs: number
): void {
  const span = current(threadId)
  if (!active || !span || span.has("paint")) return
  active.buildMs += durationMs
  span.set({ build_ms: Math.round(active.buildMs) })
  span.add("builds", 1)
}

export function threadTranscriptPainted(
  threadId: string,
  attributes: { messages: number; chunks: number }
): void {
  const span = current(threadId)
  if (!span) return
  span.mark("paint")
  span.end({ messages: attributes.messages, chunks: attributes.chunks })
  active = null
}

/** Test hook. */
export function resetThreadLoadTracking(): void {
  abandonThreadLoad("reset")
  navigated = false
}
