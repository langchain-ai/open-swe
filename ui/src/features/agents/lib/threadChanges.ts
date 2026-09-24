/**
 * The sidebar's live feed of run starts and ends (`GET /threads/events`).
 *
 * While it is connected the sidebar stops polling; whenever it is not, polling
 * picks up again, so a dropped feed only ever costs freshness.
 *
 * `EventSource` reports every failure the same way, with no status code. A
 * failure before `ready` is therefore probed with a `fetch` of the same URL,
 * aborted as soon as the headers arrive: a 401, 403 or 404 will not get better
 * by retrying, so the feed stops for the rest of the page's life and polling
 * stays on. Anything else backs off and retries like the transcript stream.
 */
import { useSyncExternalStore } from "react"

import {
  MAX_RECONNECT_ATTEMPTS,
  reconnectDelayMs,
} from "@/features/agents/lib/stream/connection"
import type { AgentThread } from "@/features/agents/lib/types"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"

export interface ThreadUpdatedEvent {
  thread: AgentThread
}

export interface ThreadChangeHandlers {
  onThreadUpdated: (thread: AgentThread) => void
  /** Changes may have been missed: refetch whatever is cached. */
  onResync: () => void
}

const FATAL_STATUSES = new Set([401, 403, 404])

let live = false
const listeners = new Set<() => void>()

function setLive(next: boolean): void {
  if (live === next) return
  live = next
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Whether the feed is connected, so polling for run status can stop. */
export function useThreadChangesLive(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => live,
    () => false
  )
}

export function threadChangesUrl(): string {
  return dashboardApiUrl("/threads/events")
}

/** The status the feed's URL answers with, or `null` when it cannot be reached. */
async function probeStatus(url: string): Promise<number | null> {
  const controller = new AbortController()
  try {
    const response = await fetch(url, {
      credentials: "include",
      signal: controller.signal,
    })
    return response.status
  } catch (error) {
    console.warn("Could not probe the thread change feed", { error })
    return null
  } finally {
    controller.abort()
  }
}

/** Keep the feed connected until the returned function is called. */
export function connectThreadChanges(
  handlers: ThreadChangeHandlers
): () => void {
  const url = threadChangesUrl()
  let disposed = false
  let source: EventSource | null = null
  let retry: ReturnType<typeof setTimeout> | null = null
  let attempt = 0

  const fail = async (ready: boolean) => {
    source?.close()
    source = null
    setLive(false)
    if (!ready) {
      const status = await probeStatus(url)
      if (disposed) return
      if (status !== null && FATAL_STATUSES.has(status)) {
        console.warn("The thread change feed is unavailable; polling instead", {
          status,
        })
        return
      }
    }
    if (attempt >= MAX_RECONNECT_ATTEMPTS) {
      console.warn("The thread change feed kept failing; polling instead")
      return
    }
    attempt += 1
    retry = setTimeout(() => {
      retry = null
      if (!disposed) open()
    }, reconnectDelayMs(attempt))
  }

  const open = () => {
    const current = new EventSource(url, { withCredentials: true })
    source = current
    let ready = false
    current.addEventListener("ready", () => {
      if (disposed || source !== current) return
      ready = true
      attempt = 0
      setLive(true)
      // Whatever changed before this subscription existed was not announced:
      // on the first connection, a list fetch may have finished just before.
      handlers.onResync()
    })
    current.addEventListener("thread-updated", (event) => {
      if (disposed || source !== current) return
      try {
        const data = JSON.parse(event.data) as ThreadUpdatedEvent
        handlers.onThreadUpdated(data.thread)
      } catch (error) {
        console.warn("Ignored an unreadable thread change", { error })
      }
    })
    current.addEventListener("resync", () => {
      if (!disposed && source === current) handlers.onResync()
    })
    current.addEventListener("error", () => {
      if (disposed || source !== current) return
      void fail(ready)
    })
  }

  open()
  return () => {
    disposed = true
    if (retry) clearTimeout(retry)
    source?.close()
    source = null
    setLive(false)
  }
}
