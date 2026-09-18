import { createContext, useContext } from "react"

import { useAgentStreamSource } from "./useAgentStreamSource"
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

/**
 * Picks the thread's source. Every thread is served by the SDK stream for now;
 * the seam is what lets a second implementation be added without any reader
 * under the thread page knowing which one it is reading.
 */
export function ThreadSourceProvider({
  threadId,
  children,
}: {
  threadId: string
  children: ReactNode
}) {
  return <StreamSource threadId={threadId}>{children}</StreamSource>
}
