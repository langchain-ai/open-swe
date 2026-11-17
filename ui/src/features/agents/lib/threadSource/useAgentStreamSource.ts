import { useCallback, useMemo } from "react"
import { useSubmissionQueue } from "@langchain/react"

import { latestContextUsage } from "@/features/agents/lib/contextUsage"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { queueEntryToTurn } from "@/features/agents/lib/queuedMessages"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { promptMessage } from "@/features/agents/lib/stream/promptMessage"
import { useAgentThreadStream } from "@/features/agents/lib/stream/useAgentThreadStream"
import {
  runStartCommand,
  startRun as postRunStart,
} from "@/features/agents/lib/transcript/api"
import type { QueuedTurn } from "@/features/agents/lib/transcript/reducer"
import { runTranscriptBuilt } from "@/lib/perf/streaming"
import { threadTranscriptBuilt } from "@/lib/perf/threadLoad"
import { perfNow } from "@/lib/perf/trace"
import { useSession } from "@/lib/session"
import { useCancelRun } from "./useCancelRun"
import type { StreamThreadSource, ThreadRunInput } from "./types"

/** The SDK stream, behind the source interface. */
export function useAgentStreamSource(threadId: string): StreamThreadSource {
  const { stream, connection, trackRunAcceptance } =
    useAgentThreadStream(threadId)
  // The thread's own cancel also stops its workers, so it runs first and this
  // only stops showing the run.
  const stop = useCancelRun(threadId, stream.disconnect)
  const login = useSession().data?.login
  const { entries: queueEntries, cancel: cancelSubmission } =
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
    async ({
      message,
      configurable,
      enqueue,
      onAccepted,
      onRejected,
    }: ThreadRunInput) => {
      if (!enqueue && stream.isLoading) {
        // `useStream` sends a message made during a run to the server's queue,
        // where it waits for the run to end. `/commands` steers it into the run.
        await postRunStart(
          threadId,
          runStartCommand({ threadId, message, configurable })
        )
        onAccepted?.()
        return
      }
      const config = Object.keys(configurable).length
        ? { configurable }
        : undefined
      const accepted = trackRunAcceptance(
        message?.id ?? null,
        onAccepted,
        onRejected
      )
      let submitError: unknown
      try {
        await stream.submit(
          message
            ? {
                messages: [
                  {
                    ...promptMessage(
                      message.text,
                      message.images,
                      message.replies
                    ),
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
      } catch (error) {
        submitError = error
      }
      const acceptance = (await accepted?.()) ?? { accepted: false }
      if (submitError && !acceptance.accepted)
        throw acceptance.refusal ?? submitError
    },
    [threadId, trackRunAcceptance, stream]
  )

  const contextUsage = useMemo(
    () => latestContextUsage(stream.messages),
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
    cancelQueued: cancelSubmission,
    isRunning: stream.isLoading,
    isHydrating: stream.isThreadLoading,
    hydration: stream.hydrationPromise,
    error: stream.error,
    isOffloading: stream.isOffloading,
    routed: stream.routed,
    connection,
    contextUsage,
    startRun,
    stop,
    hasOlder: false,
    isLoadingOlder: false,
    loadOlder,
  }
}
