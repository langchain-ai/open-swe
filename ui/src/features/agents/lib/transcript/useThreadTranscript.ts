/**
 * Live transcript for one thread: snapshot, replay from the snapshot's version,
 * then live events.
 *
 * The reduced state is kept in a module-level cache for a few minutes, so
 * coming back to a thread resumes with `after=<version>` instead of refetching
 * the whole conversation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import {
  MAX_RECONNECT_ATTEMPTS,
  reconnectDelayMs,
} from "@/features/agents/lib/stream/connection"
import { useReconnectNotice } from "@/features/agents/lib/stream/useReconnectNotice"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
} from "@/features/agents/lib/queries"
import {
  threadHydrated,
  threadHydrationFailed,
  threadTranscriptBuilt,
} from "@/lib/perf/threadLoad"
import { runTranscriptBuilt } from "@/lib/perf/streaming"
import { perfNow } from "@/lib/perf/trace"
import { fetchOlderTurns, fetchTranscript, openTranscriptEvents } from "./api"
import {
  applyEvent,
  applySnapshot,
  fromSnapshot,
  isOffloading as offloadingFromState,
  prependTurns,
  routedNotice,
  queuedTurns,
  staleWorkspaceNotice,
  toMessages,
} from "./reducer"
import type { StreamConnection } from "@/features/agents/lib/stream/connection"
import type { RunTracker } from "@/lib/perf/streaming"
import type { Message } from "@/features/agents/lib/types"
import type { TranscriptEventStream } from "./api"
import type { TranscriptState, QueuedTurn, StaleWorkspace } from "./reducer"

const CACHE_TTL_MS = 5 * 60_000

interface CacheEntry {
  state: TranscriptState
  touchedAt: number
}

const cache = new Map<string, CacheEntry>()

function cached(threadId: string): TranscriptState | null {
  const now = Date.now()
  for (const [id, entry] of cache) {
    if (now - entry.touchedAt > CACHE_TTL_MS) cache.delete(id)
  }
  return cache.get(threadId)?.state ?? null
}

const snapshots = new Map<string, Promise<TranscriptState>>()

/**
 * One snapshot fetch per thread at a time: a view remounted while its first
 * fetch is in flight joins it rather than starting another.
 */
function loadSnapshot(threadId: string): Promise<TranscriptState> {
  let pending = snapshots.get(threadId)
  if (!pending) {
    pending = fetchTranscript(threadId)
      .then(fromSnapshot)
      .finally(() => snapshots.delete(threadId))
    snapshots.set(threadId, pending)
  }
  return pending
}

/** The thread was deleted while this client was reading it. */
export class ThreadDeletedError extends Error {
  constructor() {
    super("This thread no longer exists.")
    this.name = "ThreadDeletedError"
  }
}

class Deferred {
  readonly promise: Promise<void>
  readonly resolve!: () => void
  readonly reject!: (error: unknown) => void

  constructor() {
    this.promise = new Promise<void>((resolve, reject) => {
      Object.assign(this, { resolve, reject })
    })
    // The view attaches its own handler; this one only keeps a hydration
    // failure from surfacing as an unhandled rejection when it does not.
    this.promise.catch(() => {})
  }
}

export interface ThreadTranscript {
  messages: Array<Message>
  /** Follow-ups queued behind the live run, oldest first. */
  queued: Array<QueuedTurn>
  state: TranscriptState | null
  /** The one-time snapshot fetch is in flight and there is nothing to show yet. */
  isHydrating: boolean
  /** Settles with that same fetch, so a failure can be surfaced once. */
  hydration: Promise<void>
  isRunning: boolean
  isOffloading: boolean
  routed: { route?: string; modelId?: string | null } | null
  workspaceStale: StaleWorkspace | null
  error: unknown
  connection: StreamConnection
  /** Turns older than the loaded window remain on the server. */
  hasOlder: boolean
  isLoadingOlder: boolean
  /** Load the next page of older turns. A no-op while one is in flight. */
  loadOlder: () => void
}

export function useThreadTranscript(
  threadId: string,
  options: { runTracker?: RunTracker } = {}
): ThreadTranscript {
  const runTracker = options.runTracker
  const queryClient = useQueryClient()
  const [state, setState] = useState<TranscriptState | null>(() =>
    cached(threadId)
  )
  const [error, setError] = useState<unknown>(null)
  const { connection, onReconnect, onConnected } = useReconnectNotice()
  const [isLoadingOlder, setIsLoadingOlder] = useState(false)
  const stateRef = useRef<TranscriptState | null>(state)
  const olderRequest = useRef<AbortController | null>(null)
  // A deferred, so the view can watch the one-time load for a failure the
  // same way it watched the SDK's hydration promise. One per thread.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  const hydration = useMemo(() => new Deferred(), [threadId])

  const publish = useCallback(
    (next: TranscriptState) => {
      stateRef.current = next
      cache.set(threadId, { state: next, touchedAt: Date.now() })
      setState(next)
    },
    [threadId]
  )

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setError(null)
    let disposed = false
    let stream: TranscriptEventStream | null = null
    let retry: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let frame: number | null = null
    let deadline: ReturnType<typeof setTimeout> | null = null

    const cancelPublication = () => {
      if (frame !== null) cancelAnimationFrame(frame)
      if (deadline !== null) clearTimeout(deadline)
      frame = null
      deadline = null
    }

    const flush = () => {
      cancelPublication()
      if (!disposed && stateRef.current) publish(stateRef.current)
    }

    const schedulePublication = () => {
      if (frame !== null) return
      frame = requestAnimationFrame(flush)
      deadline = setTimeout(flush, 50)
    }

    const subscribe = () => {
      const from = stateRef.current?.version ?? 0
      stream = openTranscriptEvents(threadId, from, {
        onOpen: () => {
          attempt = 0
          onConnected()
        },
        onSnapshot: (snapshot) => {
          // The frame carries the newest window, so it is merged rather than
          // swapped in: older pages the reader already loaded are settled and
          // stay.
          if (disposed) return
          cancelPublication()
          publish(applySnapshot(stateRef.current, snapshot))
        },
        onSynchronized: () => {
          if (disposed) return
          flush()
          onConnected()
        },
        onDeleted: () => {
          cancelPublication()
          // The stream is over and there is nothing left to read: drop the
          // cached transcript, surface the failure the way a missing thread
          // already surfaces, and let the sidebar refetch without this thread.
          stream = null
          cache.delete(threadId)
          stateRef.current = null
          if (disposed) return
          setState(null)
          onConnected()
          setError(new ThreadDeletedError())
          queryClient.removeQueries({
            queryKey: agentThreadKeys.detail(threadId),
          })
          invalidateAgentThreadLists(queryClient)
        },
        onEvent: (event) => {
          if (disposed) return
          const current = stateRef.current
          if (!current) return
          const next = applyEvent(current, event)
          if (next !== current) {
            stateRef.current = next
            if (
              event.event_type === "message.appended" &&
              !(
                (event.payload.text &&
                  !current.messages[event.payload.message_id]?.text) ||
                (event.payload.reasoning &&
                  !current.messages[event.payload.message_id]?.reasoning)
              )
            ) {
              schedulePublication()
            } else {
              flush()
            }
          }
          if (!runTracker) return
          const payload = event.payload
          runTracker.transcriptEvent({
            opensRun: event.event_type === "turn.started",
            text:
              event.event_type === "message.appended" &&
              Boolean("text" in payload && payload.text),
          })
          if (event.event_type === "turn.completed")
            runTracker.completed("success")
          else if (event.event_type === "turn.failed")
            runTracker.completed("error")
          else if (event.event_type === "turn.interrupted")
            runTracker.completed("interrupt")
        },
        onError: (streamError) => {
          if (disposed) return
          flush()
          stream?.close()
          stream = null
          if (attempt >= MAX_RECONNECT_ATTEMPTS) {
            onConnected()
            setError(streamError)
            return
          }
          attempt += 1
          const delay = reconnectDelayMs(attempt)
          onReconnect({ attempt, delayMs: delay })
          retry = setTimeout(() => {
            retry = null
            if (!disposed) subscribe()
          }, delay)
        },
      })
    }

    const start = async () => {
      try {
        if (!stateRef.current) {
          const loaded = await loadSnapshot(threadId)
          if (disposed) return
          publish(loaded)
        }
        threadHydrated(threadId)
        hydration.resolve()
        if (!disposed) subscribe()
      } catch (loadError) {
        if (disposed) return
        setError(loadError)
        threadHydrationFailed(threadId)
        hydration.reject(loadError)
      }
    }

    void start()

    return () => {
      disposed = true
      cancelPublication()
      if (retry) clearTimeout(retry)
      stream?.close()
      if (stateRef.current) {
        cache.set(threadId, {
          state: stateRef.current,
          touchedAt: Date.now(),
        })
      }
    }
  }, [
    hydration,
    onConnected,
    onReconnect,
    publish,
    queryClient,
    runTracker,
    threadId,
  ])

  // The one-time load already resolved by the time a delete can arrive, and a
  // settled promise cannot fail after the fact, so the view is handed a
  // rejected one instead — that is the signal it turns into a load error.
  const deleted = error instanceof ThreadDeletedError
  const hydrationPromise = useMemo(() => {
    if (!deleted) return hydration.promise
    const rejected = Promise.reject(new ThreadDeletedError())
    rejected.catch(() => {})
    return rejected
  }, [deleted, hydration])

  // A page in flight belongs to the thread that asked for it; leaving the
  // thread drops it rather than merging it into whatever is on screen next.
  useEffect(() => {
    return () => {
      olderRequest.current?.abort()
      olderRequest.current = null
    }
  }, [threadId])

  const loadOlder = useCallback(() => {
    const current = stateRef.current
    if (!current?.olderCursor || olderRequest.current) return
    const controller = new AbortController()
    olderRequest.current = controller
    setIsLoadingOlder(true)
    const cursor = current.olderCursor
    void (async () => {
      try {
        const page = await fetchOlderTurns(threadId, cursor, controller.signal)
        const latest = stateRef.current
        if (controller.signal.aborted || !latest) return
        publish(prependTurns(latest, page))
      } catch (pageError) {
        // The live stream is untouched by a failed page: surface it the way a
        // load failure surfaces and let the reader try the top again.
        if (!controller.signal.aborted) setError(pageError)
      } finally {
        if (olderRequest.current === controller) olderRequest.current = null
        if (!controller.signal.aborted) setIsLoadingOlder(false)
      }
    })()
  }, [publish, threadId])

  const messages = useMemo(() => {
    if (!state) return []
    const started = perfNow()
    const built = toMessages(state)
    const elapsed = perfNow() - started
    threadTranscriptBuilt(threadId, elapsed)
    runTranscriptBuilt(threadId, elapsed)
    return built
  }, [state, threadId])

  const queued = useMemo(() => (state ? queuedTurns(state) : []), [state])

  return {
    messages,
    queued,
    state,
    isHydrating: state === null && error === null,
    hydration: hydrationPromise,
    isRunning: state?.status === "running",
    isOffloading: state ? offloadingFromState(state) : false,
    routed: state ? routedNotice(state) : null,
    workspaceStale: state ? staleWorkspaceNotice(state) : null,
    error,
    connection,
    hasOlder: state?.olderCursor != null,
    isLoadingOlder,
    loadOlder,
  }
}
