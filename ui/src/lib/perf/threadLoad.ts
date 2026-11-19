/**
 * The `thread_load` span: from the moment the user asks for a thread until its
 * transcript is on screen.
 *
 *   navigate ─▶ detail ─▶ hydrate ─▶ paint
 *
 * `detail` is the dashboard's thread summary (`GET /threads/:id`). `hydrate` is the SDK's state fetch
 * (`GET /threads/:id/state`) that seeds the transcript, and `paint` the first
 * frame after the transcript rendered. A full page load starts the span at the
 * document's time origin so it includes bundle boot.
 *
 * Under the SDK useStream setting the state fetch starts with the summary, so
 * `hydrate` and `paint` can come first and the span can end without `detail`.
 *
 * A paint of the source's own rows before its read answered came from a cache
 * (`thread_cache_hit`). A stream open's run lookups often answer after its
 * paint, so its span ends at the paint but is sent once the open's reads
 * settle, with only the lookups added after the paint.
 */

import { useLayoutEffect, useRef } from "react"

import { subscribeRequestTimings } from "./fetchTiming"
import type { RequestTiming } from "./fetchTiming"
import { perfNow, startSpan } from "./trace"
import type { PerfAttributes, PerfThreadSource, SpanHandle } from "./trace"

const THREAD_PATH_RE =
  /^\/(agents|assistant)\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/i

/** The SDK gives up on a run lookup after 10 s. */
const LOOKUPS_MAX_WAIT_MS = 10_000

/** Which thread UI rendered the transcript, so the two can be compared on the same span. */
export type ThreadLoadUi = "agents" | "assistant"

interface PaintAttributes {
  /** Rows and chunks painted, which on a long thread are its newest turns only. */
  messages: number
  chunks: number
  /** The thread has turns older than those painted: the long threads, in either view. */
  hasOlder: boolean
  threadSource: PerfThreadSource
  /** Rows the source read or kept, not only the summary's pending ones. */
  fromSource: boolean
  /** Settles once the source's reads did: its state read and the run lookups after it. */
  hydration: Promise<unknown>
}

class ThreadLoad {
  buildMs = 0
  /** Running lookups sent before the state read answered went out with it. */
  #stateAnsweredAt: number | null = null
  #readAnswered = false
  /** The earliest send per lookup, so a reload's lookups never replace the open's. */
  readonly #lookupsSentAt = new Map<string, number>()
  #readsSettled = false
  #painted: { attributes: PerfAttributes; endedAt: number } | null = null

  constructor(
    readonly threadId: string,
    readonly span: SpanHandle
  ) {}

  /** The span while its steps still count: neither ended nor painted. */
  get open(): SpanHandle | null {
    return this.span.ended || this.#painted ? null : this.span
  }

  record(timing: RequestTiming): void {
    if (timing.kind === "thread_state") this.#stateAnsweredAt ??= perfNow()
    if (timing.kind === "running_lookup" || timing.kind === "pending_lookup") {
      const sentAt = perfNow() - timing.ttfbMs
      const name =
        timing.kind === "pending_lookup"
          ? "pending_lookup_ms"
          : sentAt < (this.#stateAnsweredAt ?? Infinity)
            ? "running_lookup_1_ms"
            : "running_lookup_2_ms"
      if (sentAt >= (this.#lookupsSentAt.get(name) ?? Infinity)) return
      this.#lookupsSentAt.set(name, sentAt)
      this.span.set({ [name]: Math.round(timing.ttfbMs) })
      this.#endIfDone()
      return
    }
    if (
      this.#painted ||
      (timing.kind !== "thread_detail" &&
        timing.kind !== "thread_state" &&
        timing.kind !== "thread_transcript")
    )
      return
    // The SDK can show its state read only once it is parsed.
    if (timing.kind === "thread_transcript" || timing.bodyMs !== undefined)
      this.#readAnswered = true
    // The snapshot is the transcript log's hydration request, so it reports under
    // the same `state_*` attributes the SDK's state fetch does.
    const prefix = timing.kind === "thread_detail" ? "detail" : "state"
    const attributes: PerfAttributes = {
      [`${prefix}_ttfb_ms`]: Math.round(timing.ttfbMs),
      [`${prefix}_status`]: timing.status,
    }
    if (timing.bodyMs !== undefined)
      attributes[`${prefix}_body_ms`] = Math.round(timing.bodyMs)
    if (timing.parseMs !== undefined)
      attributes[`${prefix}_parse_ms`] = Math.round(timing.parseMs)
    for (const entry of timing.serverTiming) {
      if (entry.duration !== null)
        attributes[`${prefix}_srv_${entry.name}_ms`] = Math.round(
          entry.duration
        )
    }
    this.span.set(attributes)
  }

  paint(attributes: PaintAttributes): void {
    this.span.mark("paint")
    this.#painted = {
      attributes: {
        messages: attributes.messages,
        chunks: attributes.chunks,
        has_older: attributes.hasOlder,
        thread_source: attributes.threadSource,
        thread_cache_hit: attributes.fromSource && !this.#readAnswered,
      },
      endedAt: perfNow(),
    }
    if (attributes.threadSource === "transcript") return this.end()
    const settle = () => {
      this.#readsSettled = true
      this.#endIfDone()
    }
    attributes.hydration.then(settle, settle)
    setTimeout(() => this.end(), LOOKUPS_MAX_WAIT_MS)
  }

  /** Sends a painted span, which ends at its paint. */
  end(): void {
    if (!this.#painted) return
    this.span.end(this.#painted.attributes, { endedAt: this.#painted.endedAt })
    if (active === this) active = null
  }

  /** A span that painted still ends, at its paint. */
  abandon(reason: string): void {
    if (this.#painted) return this.end()
    this.span.abandon(reason)
    if (active === this) active = null
  }

  /** Lookup 1 goes out with the read, outside what settles, so a sent lookup 2 means it is still due. */
  #endIfDone(): void {
    if (
      this.#readsSettled &&
      (this.#lookupsSentAt.has("running_lookup_1_ms") ||
        !this.#lookupsSentAt.has("running_lookup_2_ms"))
    )
      this.end()
  }
}

let active: ThreadLoad | null = null
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
  return active?.threadId === threadId ? active.open : null
}

function abandonThreadLoad(reason: string): void {
  active?.abandon(reason)
  active = null
}

subscribeRequestTimings((timing: RequestTiming) => {
  if (active?.threadId.toLowerCase() === timing.threadId) active.record(timing)
})

// A span held past its paint would otherwise go unsent when the page goes away.
if (typeof window !== "undefined")
  window.addEventListener("pagehide", () => active?.end())

function beginThreadLoad(
  threadId: string,
  source: "navigation" | "page_load",
  ui: ThreadLoadUi
): void {
  if (typeof window === "undefined") return
  if (current(threadId)) return
  abandonThreadLoad("superseded")
  active = new ThreadLoad(
    threadId,
    startSpan(
      "thread_load",
      { source, cold: source === "page_load", ui },
      source === "page_load" ? { startedAt: 0 } : undefined
    )
  )
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

/** The page opened without the summary, which the transcript may now beat to the screen. */
export function threadDetailPending(threadId: string): void {
  current(threadId)?.set({ detail_cached: false })
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
  attributes: PaintAttributes
): void {
  if (current(threadId)) active?.paint(attributes)
}

/**
 * Ends the span on the transcript's first frame: one rAF after the commit that
 * put `shown` on screen. A commit before the frame fires cancels and
 * reschedules it, so the frame recorded is the one that actually reached the
 * screen. `shown` is null until there is a transcript to show.
 */
export function useThreadTranscriptPaint(
  threadId: string,
  shown: ReadonlyArray<{ chunks: ReadonlyArray<unknown> }> | null,
  source: {
    kind: PerfThreadSource
    /** The thread has turns before the ones shown. */
    hasOlder: boolean
    messages: ReadonlyArray<unknown>
    hydration: Promise<unknown>
  }
): void {
  const { kind: threadSource, hasOlder, hydration } = source
  const fromSource = source.messages.length > 0
  const paintedThreadId = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (!shown || paintedThreadId.current === threadId) return
    const messages = shown.length
    const chunks = shown.reduce((sum, m) => sum + m.chunks.length, 0)
    const frame = requestAnimationFrame(() => {
      paintedThreadId.current = threadId
      threadTranscriptPainted(threadId, {
        messages,
        chunks,
        hasOlder,
        threadSource,
        fromSource,
        hydration,
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [fromSource, hasOlder, hydration, shown, threadId, threadSource])
}

/** Test hook. */
export function resetThreadLoadTracking(): void {
  abandonThreadLoad("reset")
  navigated = false
}
