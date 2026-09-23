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

/**
 * Follow-ups typed while a local thread is running. They go to the thread's
 * store queue, which the agent drains before its next model call; whatever it
 * never got to (the run ended or was stopped first) is sent as a fresh run.
 */
export function useLocalPromptQueue({
  client,
  sessionId,
  login,
  isRunning,
  submit,
}: {
  client: Client
  sessionId: string
  login: string | undefined
  isRunning: boolean
  /** Resolves `true` once the run is accepted. */
  submit: (text: string, images: Array<ImageChunk>) => Promise<boolean>
}) {
  const [state, setState] = useState<{
    sessionId: string
    items: Array<QueuedThreadMessage>
  }>({ sessionId, items: [] })
  const [error, setError] = useState<unknown>(null)
  const handoffRef = useRef(false)
  const queued = state.sessionId === sessionId ? state.items : []

  const enqueue = useCallback(
    async (text: string, images: Array<ImageChunk>) => {
      await enqueueLocalPrompt(client, sessionId, { text, images }, login)
      const createdAt = Date.now()
      setState((current) => ({
        sessionId,
        items: [
          ...(current.sessionId === sessionId ? current.items : []),
          { id: `queued-${createdAt}`, content: text, images, createdAt },
        ],
      }))
    },
    [client, login, sessionId]
  )

  useEffect(() => {
    if (isRunning || queued.length === 0 || handoffRef.current) return
    handoffRef.current = true
    // oxlint-disable-next-line react/set-state-in-effect
    setState({ sessionId, items: [] })
    // Cleared before submitting, or the new run's first model call drains the
    // store copy too and the follow-up lands twice. Restored if not accepted.
    void readLocalPromptQueue(client, sessionId)
      .then(async (pending) => {
        if (!pending) return
        await clearLocalPromptQueue(client, sessionId)
        const accepted = await submit(pending.text, pending.images).catch(
          (cause: unknown) => {
            setError(cause)
            return false
          }
        )
        if (!accepted)
          await enqueueLocalPrompt(client, sessionId, pending, login)
      })
      .catch(setError)
      .finally(() => {
        handoffRef.current = false
      })
  }, [client, isRunning, login, queued.length, sessionId, submit])

  return { queued, enqueue, error }
}
