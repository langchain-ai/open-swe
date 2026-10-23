import { useCallback, useEffect, useRef, useState } from "react"
import type { Client } from "@langchain/langgraph-sdk"

import type {
  ImageChunk,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import {
  clearLocalPromptQueue,
  enqueueLocalPrompt,
  readLocalPromptQueue,
} from "./localMessageQueue"

/** How long a release keeps retrying while the backend is still unreachable. */
const RELEASE_RETRY_MS = 3_000

/**
 * Follow-ups typed while a local thread is running. They go to the thread's
 * store queue, which the agent drains before its next model call; whatever it
 * never got to (the run ended or was stopped first) is sent as a fresh run.
 *
 * While a recovery chain holds the turn (`hold`), follow-ups stay in memory
 * only: the store would be drained by the next model call of a run that is
 * about to be replaced, so they are released into it once the connection is
 * back and merged into one prompt.
 */
export function useLocalPromptQueue({
  client,
  sessionId,
  login,
  isRunning,
  hold = false,
  submit,
}: {
  client: Client
  sessionId: string
  login: string | undefined
  isRunning: boolean
  /** A recovery chain owns the turn: queue locally instead of in the store. */
  hold?: boolean
  /** Resolves `true` once the run is accepted. */
  submit: (text: string, images: Array<ImageChunk>) => Promise<boolean>
}) {
  const [state, setState] = useState<{
    sessionId: string
    items: Array<QueuedThreadMessage>
  }>({ sessionId, items: [] })
  const [error, setError] = useState<unknown>(null)
  const [releaseAttempt, setReleaseAttempt] = useState(0)
  const handoffRef = useRef(false)
  const releasingRef = useRef(false)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const counterRef = useRef(0)
  useEffect(
    () => () => {
      if (retryTimer.current !== null) clearTimeout(retryTimer.current)
    },
    []
  )
  const items = state.sessionId === sessionId ? state.items : []
  const queued = items
  const ready = items.filter((item) => !item.held)

  const enqueue = useCallback(
    async (text: string, images: Array<ImageChunk>) => {
      if (!hold) await enqueueLocalPrompt(client, sessionId, { text, images }, login)
      counterRef.current += 1
      const createdAt = Date.now()
      setState((current) => ({
        sessionId,
        items: [
          ...(current.sessionId === sessionId ? current.items : []),
          {
            id: `queued-${createdAt}-${counterRef.current}`,
            content: text,
            images,
            createdAt,
            ...(hold ? { held: true } : {}),
          },
        ],
      }))
    },
    [client, hold, login, sessionId]
  )

  // Release what the outage held back into the store, so the normal drain (a
  // running agent picking it up, or the handoff below) takes over again. A
  // release that cannot reach the backend keeps the follow-ups held and retries:
  // a recovery that already gave up must not also eat what the user typed.
  useEffect(() => {
    if (hold || releasingRef.current) return
    const pending = items.filter((item) => item.held)
    if (pending.length === 0) return
    releasingRef.current = true
    void (async () => {
      for (const item of pending) {
        await enqueueLocalPrompt(
          client,
          sessionId,
          { text: item.content, images: item.images ?? [] },
          login
        )
      }
      setState((current) =>
        current.sessionId === sessionId
          ? {
              sessionId,
              items: current.items.map((item) =>
                item.held ? { ...item, held: false } : item
              ),
            }
          : current
      )
    })()
      .catch(() => {
        if (retryTimer.current !== null) return
        retryTimer.current = setTimeout(() => {
          retryTimer.current = null
          setReleaseAttempt((attempt) => attempt + 1)
        }, RELEASE_RETRY_MS)
      })
      .finally(() => {
        releasingRef.current = false
      })
  }, [client, hold, items, login, releaseAttempt, sessionId])

  useEffect(() => {
    if (isRunning || ready.length === 0 || handoffRef.current) return
    handoffRef.current = true
    // oxlint-disable-next-line react/set-state-in-effect
    setState((current) => ({
      sessionId,
      items:
        current.sessionId === sessionId
          ? current.items.filter((item) => item.held)
          : [],
    }))
    // The store copy is cleared only once the run is accepted, so a failed
    // handoff leaves the follow-up for the next run instead of losing it.
    void readLocalPromptQueue(client, sessionId)
      .then(async (pending) => {
        if (pending && (await submit(pending.text, pending.images))) {
          await clearLocalPromptQueue(client, sessionId)
        }
      })
      .catch(setError)
      .finally(() => {
        handoffRef.current = false
      })
  }, [client, isRunning, ready.length, sessionId, submit])

  return { queued, enqueue, error }
}
