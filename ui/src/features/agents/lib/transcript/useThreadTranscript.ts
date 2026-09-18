/**
 * Live transcript for one thread: snapshot, replay from the snapshot's version,
 * then live events.
 *
 * The reduced state is kept in a module-level cache for a few minutes, so
 * coming back to a thread resumes with `after=<version>` instead of refetching
 * the whole conversation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import {
  MAX_RECONNECT_ATTEMPTS,
  reconnectDelayMs,
} from "@/features/agents/lib/stream/streamPool"
import {
  threadHydrated,
  threadHydrationFailed,
  threadTranscriptBuilt,
} from "@/lib/perf/threadLoad"
import { runTranscriptBuilt } from "@/lib/perf/streaming"
import { perfNow } from "@/lib/perf/trace"
import { fetchTranscript, openTranscriptEvents } from "./api"
import {
  applyEvent,
  fromSnapshot,
  isOffloading as offloadingFromState,
  routedNotice,
  toMessages,
} from "./reducer"
import type { StreamConnection } from "@/features/agents/lib/stream/streamPool"
import type { RunTracker } from "@/lib/perf/streaming"
import type { Message } from "@/features/agents/lib/types"
import type { TranscriptEventStream } from "./api"
import type { TranscriptState } from "./reducer"

const CACHE_TTL_MS = 5 * 60_000

interface CacheEntry {
  state: TranscriptState
  touchedAt: number
}

const cache = new Map<string, CacheEntry>()

function sweep(now: number): void {
  for (const [threadId, entry] of cache) {
    if (now - entry.touchedAt > CACHE_TTL_MS) cache.delete(threadId)
  }
}

function cached(threadId: string): TranscriptState | null {
  sweep(Date.now())
  return cache.get(threadId)?.state ?? null
}

const LIVE: StreamConnection = { status: "live" }

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
  state: TranscriptState | null
  /** The one-time snapshot fetch is in flight and there is nothing to show yet. */
  isHydrating: boolean
  /** Settles with that same fetch, so a failure can be surfaced once. */
  hydration: Promise<void>
  isRunning: boolean
  isOffloading: boolean
  routed: { route?: string; modelId?: string | null } | null
  error: unknown
  connection: StreamConnection
}

export function useThreadTranscript(
  threadId: string,
  options: { runTracker?: RunTracker } = {}
): ThreadTranscript {
  const runTracker = options.runTracker
  const [state, setState] = useState<TranscriptState | null>(() =>
    cached(threadId)
  )
  const [error, setError] = useState<unknown>(null)
  const [connection, setConnection] = useState<StreamConnection>(LIVE)
  const stateRef = useRef<TranscriptState | null>(state)
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

    const subscribe = () => {
      const from = stateRef.current?.version ?? 0
      stream = openTranscriptEvents(threadId, from, {
        onOpen: () => {
          attempt = 0
          setConnection(LIVE)
        },
        onSnapshot: (snapshot) => {
          if (!disposed) publish(fromSnapshot(snapshot))
        },
        onSynchronized: () => {
          if (!disposed) setConnection(LIVE)
        },
        onEvent: (event) => {
          if (disposed) return
          const current = stateRef.current
          if (!current) return
          const next = applyEvent(current, event)
          if (next !== current) publish(next)
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
          stream?.close()
          stream = null
          if (attempt >= MAX_RECONNECT_ATTEMPTS) {
            setConnection(LIVE)
            setError(streamError)
            return
          }
          attempt += 1
          const delay = reconnectDelayMs(attempt)
          setConnection({
            status: "reconnecting",
            attempt,
            retryAt: Date.now() + delay,
          })
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
          const snapshot = await fetchTranscript(threadId)
          if (disposed) return
          publish(fromSnapshot(snapshot))
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
      if (retry) clearTimeout(retry)
      stream?.close()
      const entry = cache.get(threadId)
      if (entry) entry.touchedAt = Date.now()
    }
  }, [hydration, publish, runTracker, threadId])

  const messages = useMemo(() => {
    if (!state) return []
    const started = perfNow()
    const built = toMessages(state)
    const elapsed = perfNow() - started
    threadTranscriptBuilt(threadId, elapsed)
    runTranscriptBuilt(threadId, elapsed)
    return built
  }, [state, threadId])

  return {
    messages,
    state,
    isHydrating: state === null && error === null,
    hydration: hydration.promise,
    isRunning: state?.status === "running",
    isOffloading: state ? offloadingFromState(state) : false,
    routed: state ? routedNotice(state) : null,
    error,
    connection,
  }
}
