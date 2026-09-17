import { useCallback, useMemo } from "react"

import { latestContextTokens } from "@/features/agents/lib/contextUsage"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { useAgentStream } from "@/features/agents/lib/stream/AgentStreamProvider"
import { useAgentStreamConnection } from "@/features/agents/lib/stream/AgentStreamProvider"
import { promptMessage } from "@/features/agents/lib/stream/promptMessage"
import { runTranscriptBuilt } from "@/lib/perf/streaming"
import { threadTranscriptBuilt } from "@/lib/perf/threadLoad"
import { perfNow } from "@/lib/perf/trace"
import { useCancelRun } from "./useCancelRun"
import type { StreamThreadSource, ThreadRunInput } from "./types"

/** The existing SDK stream, behind the source interface. */
export function useAgentStreamSource(threadId: string): StreamThreadSource {
  const stream = useAgentStream()
  const connection = useAgentStreamConnection("cloud", threadId)
  const disconnect = useCallback(async () => {
    // `stream.stop()` only cancels server-side when this client dispatched the
    // run, so the cancel endpoint runs first and this only detaches.
    await stream.disconnect()
  }, [stream])
  const stop = useCancelRun(threadId, disconnect)

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

  return {
    kind: "stream",
    stream,
    threadId,
    messages,
    isRunning: stream.isLoading,
    isHydrating: stream.isThreadLoading,
    hydration: stream.hydrationPromise,
    error: stream.error,
    isOffloading: stream.isOffloading ?? false,
    routed: stream.routed ?? null,
    connection,
    contextTokens,
    startRun,
    stop,
  }
}
