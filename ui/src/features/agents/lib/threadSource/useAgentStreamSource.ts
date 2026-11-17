import { useCallback, useMemo, useState } from "react"
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

/** Turns a long thread paints at first and per "Load earlier", as the transcript view pages them. */
const TURN_PAGE_SIZE = 40

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

  // `useStream` holds the whole thread, but rendering every row is what makes a
  // long thread slow to open, so the view starts at the newest turns. Once the
  // thread has loaded, the window is pinned to its first row (null: the
  // thread's start), so it grows as turns arrive instead of sliding away from
  // the reader.
  const [firstRow, setFirstRow] = useState<string | null>()
  // A turn is the prompts since the last answer, then the answer.
  const turnStarts = messages.flatMap((message, index) =>
    index === 0 ||
    (message.author !== "agent" && messages[index - 1]?.author === "agent")
      ? [index]
      : []
  )
  const pinned =
    firstRow === undefined
      ? -1
      : firstRow === null
        ? 0
        : messages.findIndex((message) => message.id === firstRow)
  const shownFrom = pinned >= 0 ? pinned : (turnStarts.at(-TURN_PAGE_SIZE) ?? 0)
  const showFrom = (index: number) =>
    setFirstRow(index > 0 ? (messages[index]?.id ?? null) : null)
  if (pinned < 0 && !stream.isThreadLoading && messages.length > 0)
    showFrom(shownFrom)
  const loadOlder = () =>
    showFrom(
      turnStarts.filter((index) => index < shownFrom).at(-TURN_PAGE_SIZE) ?? 0
    )
  const loadAllOlder = useCallback(() => setFirstRow(null), [])

  return {
    kind: "stream",
    stream,
    threadId,
    messages,
    shownFrom,
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
    hasOlder: shownFrom > 0,
    isLoadingOlder: false,
    loadOlder,
    loadAllOlder,
  }
}
