import { useCallback, useMemo } from "react"
import { useSubmissionQueue } from "@langchain/react"

import { latestContextTokens } from "@/features/agents/lib/contextUsage"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { queueEntryToTurn } from "@/features/agents/lib/queuedMessages"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { promptMessage } from "@/features/agents/lib/stream/promptMessage"
import { useAgentThreadStream } from "@/features/agents/lib/stream/useAgentThreadStream"
import type { QueuedTurn } from "@/features/agents/lib/transcript/reducer"
import { runTranscriptBuilt } from "@/lib/perf/streaming"
import { threadTranscriptBuilt } from "@/lib/perf/threadLoad"
import { perfNow } from "@/lib/perf/trace"
import { useSession } from "@/lib/session"
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
  const login = useSession().data?.login
  const { entries: queueEntries, cancel: cancelQueued } =
    useSubmissionQueue(stream)
  const queued = useMemo<Array<QueuedTurn>>(
    () =>
      queueEntries
        .map((entry) => queueEntryToTurn(entry, login))
        .filter((entry): entry is QueuedTurn => entry !== null),
    [login, queueEntries]
  )

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
    async ({ message, configurable, enqueue }: ThreadRunInput) => {
      const config = Object.keys(configurable).length
        ? { configurable }
        : undefined
      // `submit()` never rejects on its own; it only routes failures to
      // `onError`. Capture and rethrow so this promise keeps the rejection
      // contract `startRun` callers rely on.
      let submitError: unknown
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
        {
          config,
          ...(enqueue ? { multitaskStrategy: "enqueue" as const } : {}),
          onError: (error: unknown) => {
            submitError = error
          },
        }
      )
      if (submitError) throw submitError
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
    queued,
    cancelQueued,
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
