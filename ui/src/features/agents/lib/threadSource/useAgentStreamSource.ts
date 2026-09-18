import { useCallback, useMemo } from "react"

import { latestContextTokens } from "@/features/agents/lib/contextUsage"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { promptMessage } from "@/features/agents/lib/stream/promptMessage"
import { useAgentThreadStream } from "@/features/agents/lib/stream/useAgentThreadStream"
import { runTranscriptBuilt } from "@/lib/perf/streaming"
import { threadTranscriptBuilt } from "@/lib/perf/threadLoad"
import { perfNow } from "@/lib/perf/trace"
import { useCancelRun } from "./useCancelRun"
import type { StreamThreadSource, ThreadRunInput } from "./types"

/** The SDK stream, behind the source interface. */
export function useAgentStreamSource(threadId: string): StreamThreadSource {
  const { stream, connection } = useAgentThreadStream({
    transport: "cloud",
    threadId,
  })
  // `stream.stop()` only cancels server-side when this client dispatched the
  // run, so the cancel endpoint runs first and this only detaches.
  const stop = useCancelRun(threadId, stream.disconnect)

  const messages = useMemo(() => {
    const started = perfNow()
    const built = streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      messageArrivalTimestamp
    )
    const elapsed = perfNow() - started
    threadTranscriptBuilt(threadId, elapsed)
    runTranscriptBuilt(threadId, elapsed)
    return built
  }, [stream.messages, stream.toolCalls, threadId])

  const startRun = useCallback(
    async ({ message, configurable }: ThreadRunInput) => {
      const config = Object.keys(configurable).length
        ? { configurable }
        : undefined
      await stream.submit(
        message
          ? {
              messages: [
                {
                  ...promptMessage(message.text, message.images),
                  id: message.id,
                },
              ],
            }
          : {},
        { config }
      )
    },
    [stream]
  )

  const contextTokens = useMemo(
    () => latestContextTokens(stream.messages),
    [stream.messages]
  )

  // `useStream` hydrates the whole thread in one go; there is no older page.
  const loadOlder = useCallback(() => {}, [])

  return {
    kind: "stream",
    stream,
    threadId,
    messages,
    isRunning: stream.isLoading,
    isHydrating: stream.isThreadLoading,
    hydration: stream.hydrationPromise,
    error: stream.error,
    isOffloading: stream.isOffloading,
    routed: stream.routed,
    connection,
    contextTokens,
    startRun,
    stop,
    hasOlder: false,
    isLoadingOlder: false,
    loadOlder,
  }
}
