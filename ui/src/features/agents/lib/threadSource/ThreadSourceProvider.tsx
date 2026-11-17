import { createContext, useContext } from "react"

import { useStreamPreference } from "@/lib/streamPreference"

import { useAgentStreamSource } from "./useAgentStreamSource"
import { useTranscriptSource } from "./useTranscriptSource"
import type { ReactNode } from "react"
import type { ThreadSource } from "./types"

const ThreadSourceContext = createContext<ThreadSource | null>(null)
const RefreshThreadSourceContext = createContext<(() => void) | null>(null)

/** Read the thread again, and join its run, after one is started outside the thread source. */
export function useRefreshThreadSource(): (() => void) | null {
  return useContext(RefreshThreadSourceContext)
}

/**
 * The thread's transcript and run controls, whichever backend serves them.
 * Everything under a thread page reads the thread through this, so the two
 * backends differ in exactly one place: which provider the page mounts.
 */
export function useThreadSource(): ThreadSource {
  const source = useOptionalThreadSource()
  if (!source)
    throw new Error("useThreadSource requires a ThreadSourceProvider")
  return source
}

/** For UI shared with pages that mount no provider, such as local threads. */
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
  const hydrate = source.stream.hydrate
  return (
    <RefreshThreadSourceContext.Provider value={() => void hydrate()}>
      <ThreadSourceContext.Provider value={source}>
        {children}
      </ThreadSourceContext.Provider>
    </RefreshThreadSourceContext.Provider>
  )
}

function TranscriptSource({
  threadId,
  children,
}: {
  threadId: string
  children: ReactNode
}) {
  const source = useTranscriptSource(threadId)
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
  const preferStream = useStreamPreference()

  if (!transcript || preferStream) {
    return <StreamSource threadId={threadId}>{children}</StreamSource>
  }

  return <TranscriptSource threadId={threadId}>{children}</TranscriptSource>
}
