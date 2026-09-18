import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"

import { AgentsApiError } from "@/features/agents/lib/api"
import { useAgentStreamSource } from "./useAgentStreamSource"
import { useTranscriptSource } from "./useTranscriptSource"
import type { ReactNode } from "react"
import type { ThreadSource } from "./types"

const ThreadSourceContext = createContext<ThreadSource | null>(null)

/**
 * The thread's transcript and run controls, whichever backend serves them.
 * Everything under a thread page reads the thread through this, so the two
 * backends differ in exactly one place: which provider the page mounts.
 */
export function useThreadSource(): ThreadSource {
  const source = useContext(ThreadSourceContext)
  if (!source)
    throw new Error("useThreadSource requires a ThreadSourceProvider")
  return source
}

/** Null outside a thread page (the home composer), where there is no thread yet. */
export function useOptionalThreadSource(): ThreadSource | null {
  return useContext(ThreadSourceContext)
}

function StreamSource({
  threadId,
  children,
}: {
  threadId: string
  children: ReactNode
}) {
  const source = useAgentStreamSource(threadId)
  return (
    <ThreadSourceContext.Provider value={source}>
      {children}
    </ThreadSourceContext.Provider>
  )
}

/** The event log has no transcript for a thread whose metadata says it does. */
function isTranscriptUnavailable(error: unknown): boolean {
  return (
    error instanceof AgentsApiError &&
    error.status === 404 &&
    error.message === "transcript_unavailable"
  )
}

function TranscriptSource({
  threadId,
  children,
  onUnavailable,
}: {
  threadId: string
  children: ReactNode
  onUnavailable: () => void
}) {
  const source = useTranscriptSource(threadId)
  useEffect(() => {
    let active = true
    source.hydration.catch((error: unknown) => {
      if (active && isTranscriptUnavailable(error)) onUnavailable()
    })
    return () => {
      active = false
    }
  }, [onUnavailable, source.hydration])
  return (
    <ThreadSourceContext.Provider value={source}>
      {children}
    </ThreadSourceContext.Provider>
  )
}

/**
 * Picks the thread's source. The component identity differs per kind, so each
 * implementation owns its own hooks and neither runs for the other's threads:
 * a transcript thread never opens an SDK stream, because only `StreamSource`
 * mounts one and the page renders this once the thread detail has resolved.
 */
export function ThreadSourceProvider({
  threadId,
  transcript,
  children,
}: {
  threadId: string
  /** True for threads whose metadata says the event log serves them. */
  transcript: boolean
  children: ReactNode
}) {
  // Metadata can claim a transcript the log does not hold (a row deleted, or
  // a thread stamped before its first event landed). Such a thread reads
  // LangGraph state like any thread from before the event log.
  const [unavailable, setUnavailable] = useState(false)
  const fallBack = useCallback(() => setUnavailable(true), [])
  return transcript && !unavailable ? (
    <TranscriptSource threadId={threadId} onUnavailable={fallBack}>
      {children}
    </TranscriptSource>
  ) : (
    <StreamSource threadId={threadId}>{children}</StreamSource>
  )
}
