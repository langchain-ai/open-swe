/**
 * Lazy thread hydration: paint the transcript from a skeleton, fill in tool
 * results afterwards.
 *
 * The SDK seeds `useStream` from one `client.threads.getState()` call, and on
 * a long thread most of that payload is tool output nobody sees until a card
 * is expanded. `withLazyHydration` swaps the SDK's state read for the
 * dashboard's `?view=skeleton` variant, where each large tool result is a
 * short preview plus a marker, then fetches the full results from
 * `/state/tool-results` and publishes them here once the transcript has
 * painted. `streamMessagesToUi` overlays them on the marked messages.
 *
 * The client patch is a local stand-in for a hydration hook the SDK does not
 * offer yet; it only intercepts the single-argument read `hydrate()` makes.
 */

import { create } from "zustand"
import type { Client } from "@langchain/langgraph-sdk"
import type { ToolMessage } from "@langchain/core/messages"

import { threadLoadLazy } from "@/lib/perf/threadLoad"
import { startSpan } from "@/lib/perf/trace"

export const LAZY_MARKER_KEY = "open_swe_lazy"
const FLAG_STORAGE_KEY = "open-swe.lazy-hydration"
const FLAG_QUERY_KEY = "lazy"
/** How long a loaded batch waits for the first paint before applying anyway. */
const PAINT_WAIT_MS = 1_500
/** Threads whose loaded results stay in memory. */
const RETAINED_THREADS = 3

export interface LazyMarker {
  truncated: boolean
  size: number
}

export interface LazyToolResult {
  content: unknown
  artifact: unknown
  status: string | null
}

interface SkeletonSummary {
  deferred: number
  deferred_bytes: number
}

interface ToolResultsResponse {
  results: Record<string, LazyToolResult>
}

/**
 * `?lazy=0` turns the skeleton off for a before/after comparison and `?lazy=1`
 * back on; either persists in `localStorage`. On by default.
 */
export function lazyHydrationEnabled(): boolean {
  if (typeof window === "undefined") return false
  try {
    const param = new URLSearchParams(window.location.search).get(
      FLAG_QUERY_KEY
    )
    if (param !== null) {
      const enabled = !["0", "false", "off"].includes(param.toLowerCase())
      window.localStorage.setItem(FLAG_STORAGE_KEY, enabled ? "1" : "0")
      return enabled
    }
    return window.localStorage.getItem(FLAG_STORAGE_KEY) !== "0"
  } catch {
    return true
  }
}

/** The marker a skeleton hydration left on a trimmed tool message, if any. */
export function lazyMarker(message: ToolMessage): LazyMarker | null {
  const marker = message.additional_kwargs[LAZY_MARKER_KEY]
  if (
    marker &&
    typeof marker === "object" &&
    (marker as LazyMarker).truncated === true &&
    typeof (marker as LazyMarker).size === "number"
  ) {
    return marker as LazyMarker
  }
  return null
}

export interface LazyToolResultsState {
  /** Loaded results by thread id, then by tool call id. */
  results: Record<string, Record<string, LazyToolResult>>
  loading: Record<string, boolean>
  receive(threadId: string, results: Record<string, LazyToolResult>): void
  setLoading(threadId: string, loading: boolean): void
}

export const useLazyToolResults = create<LazyToolResultsState>((set) => ({
  results: {},
  loading: {},
  receive(threadId, results) {
    set((state) => {
      const kept = Object.entries(state.results).filter(
        ([id]) => id !== threadId
      )
      const pruned = kept.slice(Math.max(0, kept.length - RETAINED_THREADS + 1))
      return {
        results: { ...Object.fromEntries(pruned), [threadId]: results },
      }
    })
  },
  setLoading(threadId, loading) {
    set((state) => ({ loading: { ...state.loading, [threadId]: loading } }))
  },
}))

const paintWaiters = new Map<
  string,
  { promise: Promise<void>; resolve: () => void }
>()

function paintWaiter(threadId: string) {
  let waiter = paintWaiters.get(threadId)
  if (!waiter) {
    let resolve = () => {}
    const promise = new Promise<void>((done) => {
      resolve = done
    })
    waiter = { promise, resolve }
    paintWaiters.set(threadId, waiter)
  }
  return waiter
}

/** The transcript view reports its first frame here so results apply after it. */
export function markTranscriptPainted(threadId: string): void {
  paintWaiter(threadId).resolve()
}

function afterPaint(threadId: string): Promise<void> {
  return Promise.race([
    paintWaiter(threadId).promise,
    new Promise<void>((done) => setTimeout(done, PAINT_WAIT_MS)),
  ])
}

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string
  ) {
    super(message)
  }
}

function stateUrl(apiUrl: string, threadId: string, suffix = ""): string {
  return `${apiUrl}/threads/${encodeURIComponent(threadId)}/state${suffix}`
}

async function loadToolResults(
  threadId: string,
  apiUrl: string,
  fetchImpl: typeof fetch,
  summary: SkeletonSummary
): Promise<void> {
  const store = useLazyToolResults.getState()
  if (store.loading[threadId]) return
  store.setLoading(threadId, true)
  const span = startSpan("thread_tool_results", {
    deferred: summary.deferred,
    deferred_kb: Math.round(summary.deferred_bytes / 1024),
  })
  try {
    const response = await fetchImpl(
      stateUrl(apiUrl, threadId, "/tool-results"),
      { headers: { Accept: "application/json" } }
    )
    if (!response.ok) {
      throw new HttpError(response.status, `tool results ${response.status}`)
    }
    const body = (await response.json()) as ToolResultsResponse
    span.mark("fetched")
    await afterPaint(threadId)
    useLazyToolResults.getState().receive(threadId, body.results)
    span.end({ results: Object.keys(body.results).length })
  } catch (error) {
    span.abandon("failed")
    console.warn("[lazy-hydration] tool results failed", { threadId, error })
  } finally {
    useLazyToolResults.getState().setLoading(threadId, false)
  }
}

type GetState = (threadId: string, ...rest: Array<unknown>) => Promise<unknown>

/**
 * Point the SDK's hydration read at the skeleton endpoint. Reads with a
 * checkpoint or options fall through to the SDK, as does everything when the
 * flag is off.
 */
export function withLazyHydration(
  client: Client,
  apiUrl: string,
  fetchImpl: typeof fetch
): Client {
  const threads = client.threads as unknown as { getState: GetState }
  const original: GetState = threads.getState.bind(client.threads)
  threads.getState = async (threadId, ...rest) => {
    if (rest.some((arg) => arg !== undefined) || !lazyHydrationEnabled()) {
      threadLoadLazy(threadId, null)
      return original(threadId, ...rest)
    }
    const response = await fetchImpl(
      stateUrl(apiUrl, threadId, "?view=skeleton"),
      { headers: { Accept: "application/json" } }
    )
    if (!response.ok) {
      throw new HttpError(response.status, `thread state ${response.status}`)
    }
    const payload = (await response.json()) as Record<string, unknown>
    const summary = payload[LAZY_MARKER_KEY] as SkeletonSummary | undefined
    threadLoadLazy(threadId, summary ?? { deferred: 0, deferred_bytes: 0 })
    if (summary && summary.deferred > 0) {
      void loadToolResults(threadId, apiUrl, fetchImpl, summary)
    }
    return payload
  }
  return client
}
