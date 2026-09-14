/**
 * The `thread_load` span: from the moment the user asks for a thread until its
 * transcript is on screen.
 *
 *   navigate ─▶ detail ─▶ hydrate ─▶ paint
 *
 * `detail` is the dashboard's thread summary (`GET /threads/:id`), `hydrate`
 * the SDK's state fetch (`GET /threads/:id/state`) that seeds the transcript,
 * and `paint` the first frame after the transcript rendered. A full page load
 * starts the span at the document's time origin so it includes bundle boot.
 */

import { subscribeRequestTimings } from "./fetchTiming"
import type { RequestTiming } from "./fetchTiming"
import { startSpan } from "./trace"
import type { PerfAttributes, SpanHandle } from "./trace"

export type ThreadLoadSource = "navigation" | "page_load"

interface ActiveThreadLoad {
  threadId: string
  span: SpanHandle
}

const THREAD_PATH_RE =
  /^\/agents\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i

let active: ActiveThreadLoad | null = null
let navigated = false
let requestTimingsSubscribed = false

export function threadIdFromPath(pathname: string): string | null {
  return THREAD_PATH_RE.exec(pathname)?.[1] ?? null
}

function current(threadId: string): SpanHandle | null {
  if (!active || active.threadId !== threadId || active.span.ended) return null
  return active.span
}

export function abandonThreadLoad(reason: string): void {
  if (active && !active.span.ended) active.span.abandon(reason)
  active = null
}

function subscribeRequestTimingsOnce(): void {
  if (requestTimingsSubscribed) return
  requestTimingsSubscribed = true
  subscribeRequestTimings(onRequestTiming)
}

function onRequestTiming(timing: RequestTiming): void {
  if (!active || active.span.ended) return
  if (timing.kind !== "thread_detail" && timing.kind !== "thread_state") return
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
}

export function beginThreadLoad(
  threadId: string,
  source: ThreadLoadSource
): void {
  if (typeof window === "undefined") return
  if (current(threadId)) return
  abandonThreadLoad("superseded")
  subscribeRequestTimingsOnce()
  active = {
    threadId,
    span: startSpan(
      "thread_load",
      { source, cold: source === "page_load" },
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
  const to = threadIdFromPath(toPathname)
  const from = fromPathname ? threadIdFromPath(fromPathname) : null
  if (to && to !== from) beginThreadLoad(to, "navigation")
  else if (!to && active) abandonThreadLoad("navigated_away")
}

/**
 * Page hook: makes sure a span exists once the thread page mounts. Without a
 * prior client navigation this is a full page load, measured from time origin.
 */
export function ensureThreadLoad(threadId: string): void {
  if (current(threadId)) return
  beginThreadLoad(threadId, navigated ? "navigation" : "page_load")
}

export function threadDetailResolved(
  threadId: string,
  attributes: { cached: boolean }
): void {
  current(threadId)?.mark("detail", { detail_cached: attributes.cached })
}

export function threadDetailFailed(threadId: string): void {
  if (current(threadId)) abandonThreadLoad("detail_failed")
}

export function threadHydrated(
  threadId: string,
  attributes: { messages: number }
): void {
  current(threadId)?.mark("hydrate", { hydrated_messages: attributes.messages })
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
  if (!span || span.has("paint")) return
  span.add("build_ms", durationMs)
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
