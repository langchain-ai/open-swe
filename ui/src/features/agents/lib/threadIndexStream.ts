/**
 * The sidebar's live feed: `thread_index` changes for the signed-in viewer,
 * over SSE, so the thread lists stop polling while it is connected.
 *
 * Whether a feed is live is kept in a small store, so every list hook can turn
 * its polling off while it is and back on the moment it drops.
 */

import { useSyncExternalStore } from "react"

import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import type { ThreadsPageParams } from "./api"
import type { AgentThread } from "./types"

/** The filters the feed applies; the same names `/threads/page` takes. */
export type ThreadIndexStreamParams = Pick<
  ThreadsPageParams,
  | "all"
  | "resolved"
  | "source"
  | "scope"
  | "automationId"
  | "repo"
  | "ownerless"
>

export interface ThreadUpsertedEvent {
  seq: number
  thread: AgentThread
}

export type ThreadRemovalReason = "deleted" | "unreadable" | "filtered"

export interface ThreadRemovedEvent {
  /** Null for a deleted thread. */
  seq: number | null
  thread_id: string
  reason: ThreadRemovalReason
}

export interface ThreadIndexHeadEvent {
  seq: number
}

export interface ThreadIndexStreamHandlers {
  onUpserted: (event: ThreadUpsertedEvent) => void
  onRemoved: (event: ThreadRemovedEvent) => void
  /** Replay is done; the feed is live from `seq`. */
  onSynchronized: (event: ThreadIndexHeadEvent) => void
  /** Changes were missed; refetch the lists. The feed stays open. */
  onResync: (event: ThreadIndexHeadEvent) => void
  onOpen?: () => void
  /**
   * The connection dropped or a frame was unreadable. `unavailable` means the
   * server refused the request outright (the feed is switched off, or the
   * session is gone), so reopening would only be refused again.
   */
  onError: (error: unknown, details: { unavailable: boolean }) => void
}

export interface ThreadIndexEventStream {
  close: () => void
}

function streamQuery(params: ThreadIndexStreamParams, after: number): string {
  const search = new URLSearchParams({ after: String(after) })
  if (params.all != null) search.set("all", String(params.all))
  if (params.resolved != null) search.set("resolved", String(params.resolved))
  if (params.source) search.set("source", params.source)
  if (params.scope) search.set("scope", params.scope)
  if (params.automationId) search.set("automation_id", params.automationId)
  if (params.repo) search.set("repo", params.repo)
  if (params.ownerless != null)
    search.set("ownerless", String(params.ownerless))
  return search.toString()
}

/**
 * Subscribe to changes after `after` (0: only the head). Like the transcript
 * feed, the session cookie is the only credential `EventSource` can carry, and
 * the caller reopens a dropped stream itself so it resumes from its own `seq`.
 */
export function openThreadIndexEvents(
  params: ThreadIndexStreamParams,
  after: number,
  handlers: ThreadIndexStreamHandlers
): ThreadIndexEventStream {
  const url = `${dashboardApiUrl("/threads/index/events")}?${streamQuery(params, after)}`
  const source = new EventSource(url, { withCredentials: true })
  let closed = false
  let opened = false

  const close = () => {
    closed = true
    source.close()
  }

  const on = <T>(name: string, apply: (value: T) => void) =>
    source.addEventListener(name, (event: MessageEvent<string>) => {
      if (closed) return
      try {
        apply(JSON.parse(event.data) as T)
      } catch (error) {
        handlers.onError(error, { unavailable: false })
      }
    })

  on<ThreadUpsertedEvent>("thread-upserted", handlers.onUpserted)
  on<ThreadRemovedEvent>("thread-removed", handlers.onRemoved)
  on<ThreadIndexHeadEvent>("synchronized", handlers.onSynchronized)
  on<ThreadIndexHeadEvent>("resync", handlers.onResync)
  source.addEventListener("open", () => {
    if (closed) return
    opened = true
    handlers.onOpen?.()
  })
  source.addEventListener("error", () => {
    if (closed) return
    // `EventSource` hides the status code. A response that is not a stream
    // (404 with the feed off, 401, 403) closes it without ever opening;
    // a network drop leaves it reconnecting instead.
    const refused = !opened && source.readyState === EventSource.CLOSED
    handlers.onError(
      new Error(
        refused
          ? "Thread index stream refused"
          : "Thread index stream interrupted"
      ),
      { unavailable: refused }
    )
  })

  return { close }
}

let liveStreams = 0
const listeners = new Set<() => void>()

function notify() {
  for (const listener of listeners) listener()
}

/** Count one feed as live until the returned function is called. */
export function markThreadIndexLive(): () => void {
  liveStreams += 1
  notify()
  let released = false
  return () => {
    if (released) return
    released = true
    liveStreams -= 1
    notify()
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function isLive() {
  return liveStreams > 0
}

/** Whether a sidebar feed is connected and synchronized, so polling can stop. */
export function useThreadIndexLive(): boolean {
  return useSyncExternalStore(subscribe, isLive, () => false)
}
