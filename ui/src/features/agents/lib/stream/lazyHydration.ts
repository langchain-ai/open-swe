/**
 * Lazy thread hydration: paint the transcript from a skeleton, fill in the
 * rest afterwards.
 *
 * The SDK seeds `useStream` from one `client.threads.getState()` call. The
 * dashboard's `?view=skeleton` variant of that read keeps only what the
 * transcript needs for first paint: user and assistant text, tool calls and
 * their arguments, statuses and timestamps. Tool results and artifacts,
 * reasoning text, pasted images and non-message state are left out, each
 * trimmed message carrying a marker, and fetched from `/state/deferred` once
 * the transcript has painted. `streamMessagesToUi` overlays them on the
 * marked messages.
 *
 * `withLazyHydration` swaps the SDK client's own state read for that flow; it
 * is a local stand-in for a hydration hook the SDK does not offer yet and only
 * intercepts the single-argument read `hydrate()` makes.
 */

import { create } from "zustand"
import type { Client } from "@langchain/langgraph-sdk"

import { threadLoadLazy } from "@/lib/perf/threadLoad"
import { startSpan } from "@/lib/perf/trace"

export const LAZY_MARKER_KEY = "open_swe_lazy"
const FLAG_STORAGE_KEY = "open-swe.lazy-hydration"
const FLAG_QUERY_KEY = "lazy"
/** How long a loaded batch waits for the first paint before applying anyway. */
const PAINT_WAIT_MS = 1_500
/** Threads whose loaded parts stay in memory. */
const RETAINED_THREADS = 3

export type DeferredPart = "content" | "artifact" | "reasoning" | "images"

export interface LazyMarker {
  truncated: boolean
  size: number
  parts: Array<DeferredPart>
}

export interface LazyToolResult {
  content: unknown
  artifact: unknown
  status: string | null
}

/** What the skeleton left out, addressed the way the transcript looks things up. */
export interface DeferredParts {
  /** By tool call id. */
  tool_results: Record<string, LazyToolResult>
  /** Reasoning text by assistant message id. */
  reasoning: Record<string, string>
  /** Image content blocks by human message id. */
  images: Record<string, Array<unknown>>
}

export interface SkeletonSummary {
  deferred: number
  deferred_bytes: number
  kept_bytes: number
  categories: Record<string, number>
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

/** The marker a skeleton hydration left on a trimmed message, if any. */
export function lazyMarker(message: {
  additional_kwargs: Record<string, unknown>
}): LazyMarker | null {
  const marker = message.additional_kwargs[LAZY_MARKER_KEY]
  if (
    marker &&
    typeof marker === "object" &&
    (marker as LazyMarker).truncated === true &&
    typeof (marker as LazyMarker).size === "number"
  ) {
    const parts = (marker as Partial<LazyMarker>).parts
    return {
      truncated: true,
      size: (marker as LazyMarker).size,
      parts: Array.isArray(parts) ? parts : ["content"],
    }
  }
  return null
}

export interface DeferredPartsState {
  /** Loaded parts by thread id. */
  parts: Record<string, DeferredParts>
  loading: Record<string, boolean>
  receive(threadId: string, parts: DeferredParts): void
  setLoading(threadId: string, loading: boolean): void
}

export const useDeferredParts = create<DeferredPartsState>((set) => ({
  parts: {},
  loading: {},
  receive(threadId, parts) {
    set((state) => {
      const kept = Object.entries(state.parts).filter(([id]) => id !== threadId)
      const pruned = kept.slice(Math.max(0, kept.length - RETAINED_THREADS + 1))
      return { parts: { ...Object.fromEntries(pruned), [threadId]: parts } }
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

/** The transcript view reports its first frame here so parts apply after it. */
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

async function loadDeferredParts(
  threadId: string,
  apiUrl: string,
  fetchImpl: typeof fetch,
  summary: SkeletonSummary,
  checkpointId: string | undefined
): Promise<void> {
  const store = useDeferredParts.getState()
  if (store.loading[threadId]) return
  store.setLoading(threadId, true)
  const span = startSpan("thread_tool_results", {
    deferred: summary.deferred,
    deferred_kb: Math.round(summary.deferred_bytes / 1024),
  })
  try {
    const query = checkpointId
      ? `?checkpoint_id=${encodeURIComponent(checkpointId)}`
      : ""
    const response = await fetchImpl(
      stateUrl(apiUrl, threadId, `/deferred${query}`),
      { headers: { Accept: "application/json" } }
    )
    if (!response.ok) {
      throw new HttpError(response.status, `deferred parts ${response.status}`)
    }
    const body = (await response.json()) as DeferredParts
    span.mark("fetched")
    await afterPaint(threadId)
    useDeferredParts.getState().receive(threadId, body)
    span.end({
      results: Object.keys(body.tool_results).length,
      reasoning: Object.keys(body.reasoning).length,
      images: Object.keys(body.images).length,
    })
  } catch (error) {
    span.abandon("failed")
    console.warn("[lazy-hydration] deferred parts failed", { threadId, error })
  } finally {
    useDeferredParts.getState().setLoading(threadId, false)
  }
}

type GetState = (threadId: string, ...rest: Array<unknown>) => Promise<unknown>

interface SkeletonPayload {
  checkpoint?: { checkpoint_id?: string } | null
  [LAZY_MARKER_KEY]?: SkeletonSummary
}

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
    const payload = (await response.json()) as SkeletonPayload
    const summary = payload[LAZY_MARKER_KEY]
    threadLoadLazy(threadId, summary ?? null)
    if (summary && summary.deferred > 0) {
      void loadDeferredParts(
        threadId,
        apiUrl,
        fetchImpl,
        summary,
        payload.checkpoint?.checkpoint_id
      )
    }
    return payload
  }
  return client
}
