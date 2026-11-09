import { useCallback, useEffect, useRef, useState } from "react"

import { APIError } from "../core/apiClient"
import type { BranchDiff } from "../core/models"
import type { TranscriptItem } from "../core/transcript"
import { TranscriptLog } from "../core/transcriptLog"
import type { AgentStore } from "../state/agentStore"

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener("abort", () => {
      clearTimeout(timer)
      resolve()
    })
  })

export interface ThreadFollower {
  /** Null until the first load finishes. */
  items: TranscriptItem[] | null
  /** The live event log, for threads that have one; older threads poll their graph state. */
  log: TranscriptLog | null
  diff: BranchDiff | null
  olderFailed: boolean
  loadOlder: () => Promise<void>
  /** After sending, a thread without a log reloads; a live log shows the turn itself. */
  afterSend: () => Promise<void>
}

/**
 * Load a thread, then follow it live: through the transcript event log when it
 * has one, else by polling its graph state.
 */
export function useThreadFollower(
  store: AgentStore,
  threadID: string
): ThreadFollower {
  const [items, setItems] = useState<TranscriptItem[] | null>(null)
  const [diff, setDiff] = useState<BranchDiff | null>(null)
  const [olderFailed, setOlderFailed] = useState(false)
  const [log, setLog] = useState<TranscriptLog | null>(null)
  const [, setLogRevision] = useState(0)
  const logRef = useRef<TranscriptLog | null>(null)
  const loadingOlder = useRef(false)

  /** Show the log's current transcript; the log mutates in place, so a revision forces the render. */
  const publish = useCallback(() => {
    setLog(logRef.current)
    setItems(logRef.current ? logRef.current.transcript() : [])
    setLogRevision((revision) => revision + 1)
  }, [])

  const loadDiff = useCallback(async () => {
    try {
      setDiff(await store.service.branchDiff(threadID))
    } catch {
      // The changes card stays hidden.
    }
  }, [store, threadID])

  /** A thread that was just started may not be listed yet; its seeded row stands in. */
  const refreshDetail = useCallback(async () => {
    try {
      store.upsert(await store.service.thread(threadID))
    } catch (error) {
      if (!(error instanceof APIError && error.status === 404))
        store.report(error)
    }
  }, [store, threadID])

  const loadLegacy = useCallback(async () => {
    try {
      setItems(await store.service.legacyTranscript(threadID))
    } catch (error) {
      setItems((current) => current ?? [])
      store.report(error)
    }
  }, [store, threadID])

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller

    /**
     * Apply live events; when the stream ends or drops, reopen it after the
     * last applied version, backing off while it keeps failing.
     */
    const stream = async () => {
      let failures = 0
      while (!signal.aborted && logRef.current) {
        let ended = false
        try {
          const end = await store.service.transcriptEvents(
            threadID,
            logRef.current.version,
            (message) => {
              failures = 0
              switch (message.kind) {
                case "event":
                  if (logRef.current?.apply(message.event)) {
                    refreshDetail()
                    loadDiff()
                  }
                  break
                case "snapshot":
                  logRef.current = new TranscriptLog(message.snapshot)
                  break
                case "synchronized":
                  return
                case "ended":
                  ended = true
                  return
              }
              publish()
            },
            signal
          )
          // A rotated stream closed only to bound its memory; reopen it at once.
          if (end === "rotated") continue
        } catch (error) {
          if (signal.aborted) return
          if (error instanceof APIError && error.isUnauthorized) {
            store.report(error)
            return
          }
          failures += 1
        }
        if (ended) return
        await sleep(Math.min(30, 2 ** Math.min(failures, 5)) * 1000, signal)
      }
    }

    const pollLegacy = async () => {
      await loadLegacy()
      while (!signal.aborted) {
        const wasRunning = store.thread(threadID)?.status === "running"
        await sleep(wasRunning ? 3000 : 15_000, signal)
        if (signal.aborted) break
        await refreshDetail()
        await loadLegacy()
        if (wasRunning && store.thread(threadID)?.status !== "running")
          await loadDiff()
      }
    }

    const follow = async () => {
      store.markViewed(threadID)
      refreshDetail()
      loadDiff()
      try {
        const loaded = await store.service.transcriptLog(threadID)
        if (signal.aborted) return
        if (loaded) {
          logRef.current = loaded
          publish()
          await stream()
        } else {
          await pollLegacy()
        }
      } catch (error) {
        if (signal.aborted) return
        setItems((current) => current ?? [])
        store.report(error)
      }
    }

    follow()
    return () => controller.abort()
  }, [store, threadID, publish, refreshDetail, loadDiff, loadLegacy])

  const loadOlder = useCallback(async () => {
    const cursor = logRef.current?.olderCursor
    if (loadingOlder.current || !cursor) return
    loadingOlder.current = true
    try {
      const page = await store.service.olderTurns(threadID, cursor)
      logRef.current?.prepend(page)
      setOlderFailed(false)
      publish()
    } catch {
      setOlderFailed(true)
    } finally {
      loadingOlder.current = false
    }
  }, [store, threadID, publish])

  const afterSend = useCallback(async () => {
    if (!logRef.current) await loadLegacy()
  }, [loadLegacy])

  return {
    items,
    log,
    diff,
    olderFailed,
    loadOlder,
    afterSend,
  }
}
