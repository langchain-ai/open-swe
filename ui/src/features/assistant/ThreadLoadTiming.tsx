import { useEffect, useLayoutEffect, useRef } from "react"
import { useAuiState } from "@assistant-ui/react"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadDetailResolved,
  threadHydrated,
  threadTranscriptPainted,
} from "@/lib/perf/threadLoad"
import { useThreadMetadata } from "./AssistantProvider"

/** Reports the `thread_load` stages the existing thread page reports, tagged `ui=assistant`. Key it by thread. */
export function ThreadLoadTiming({ threadId }: { threadId: string }) {
  const loading = useAuiState((state) => state.thread.isLoading)
  const messages = useAuiState((state) => state.thread.messages.length)
  const chunks = useAuiState((state) =>
    state.thread.messages.reduce(
      (sum, message) => sum + message.content.length,
      0
    )
  )
  const metadata = useThreadMetadata()
  const hasDetail = metadata.data !== undefined
  const detailCachedOnMount = useRef(hasDetail)

  useEffect(() => {
    ensureThreadLoad(threadId, "assistant")
  }, [threadId])

  useEffect(() => {
    if (hasDetail)
      threadDetailResolved(threadId, { cached: detailCachedOnMount.current })
    else if (metadata.isError) threadDetailFailed(threadId)
  }, [hasDetail, metadata.isError, threadId])

  useEffect(() => {
    if (!loading) threadHydrated(threadId)
  }, [loading, threadId])

  const painted = useRef(false)
  useLayoutEffect(() => {
    if (loading || painted.current) return
    const frame = requestAnimationFrame(() => {
      painted.current = true
      threadTranscriptPainted(threadId, { messages, chunks })
    })
    return () => cancelAnimationFrame(frame)
  }, [chunks, loading, messages, threadId])

  return null
}
