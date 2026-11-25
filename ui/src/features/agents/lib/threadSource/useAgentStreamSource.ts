import { useCallback, useMemo, useState } from "react"
import { useSubmissionQueue } from "@langchain/react"
import {
  isSilentRun,
  isWake,
} from "@/features/agents/components/messages/quietRuns"

import { latestContextUsage } from "@/features/agents/lib/contextUsage"
import { MessageClock } from "@/features/agents/lib/messageTimestamps"
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

/** Turns a long thread paints on first open, kept small so it mounts fast. */
const INITIAL_TURN_WINDOW = 12
/** Turns revealed per "Load earlier", as the transcript view pages them. */
const TURN_PAGE_SIZE = 40

/** The SDK stream, behind the source interface. */
export function useAgentStreamSource(threadId: string): StreamThreadSource {
  const { stream, client, connection, trackRunAcceptance } =
    useAgentThreadStream(threadId)
  // The thread's own cancel also stops its workers, so it runs first and this
  // only stops showing the run.
  const stop = useCancelRun(threadId, stream.disconnect)
  const viewer = useSession().data ?? undefined
  const { entries: queueEntries, cancel: cancelSubmission } =
    useSubmissionQueue(stream)
  const queued = useMemo<Array<QueuedTurn>>(
    () =>
      queueEntries
        .map((entry) => queueEntryToTurn(entry, viewer))
        .filter((entry): entry is QueuedTurn => entry !== null),
    [viewer, queueEntries]
  )
  const settleQueue = useCallback(async () => {
    if (queueEntries.length === 0) return
    let gone: (entry: (typeof queueEntries)[number]) => boolean
    try {
      // Pending first: a kept run that starts between the two reads then
      // shows up in the second.
      const pending = await client.runs.list(threadId, {
        status: "pending",
        limit: 100,
        select: ["run_id"],
      })
      const running = await client.runs.list(threadId, {
        status: "running",
        limit: 100,
        select: ["run_id"],
      })
      const held = new Set([...pending, ...running].map((run) => run.run_id))
      gone = (entry) => !held.has(entry.runId ?? entry.id)
    } catch {
      // Without the server's list, only the viewer's own runs are known to be
      // cancelled: the stop always cancels those.
      const own = new Set(
        queued
          .filter((turn) => viewer && turn.senderLogin === viewer.login)
          .map((turn) => turn.turnId)
      )
      gone = (entry) => own.has(entry.id)
    }
    await Promise.allSettled(
      queueEntries.filter(gone).map((entry) => cancelSubmission(entry.id))
    )
  }, [cancelSubmission, client, queueEntries, queued, threadId, viewer])
  // The SDK has no local-only removal: dropping the entry cancels its run
  // again, which the server refuses as already withdrawn.
  const forgetQueued = useCallback(
    async (id: string) => {
      try {
        await cancelSubmission(id)
      } catch (error) {
        console.debug("Repeated cancel of a withdrawn queued run failed", error)
      }
    },
    [cancelSubmission]
  )

  const [clock] = useState(() => new MessageClock())
  const live = stream.isLoading
  const messages = useMemo(() => {
    const started = perfNow()
    const built = streamMessagesToUi(
      stream.messages,
      stream.toolCalls,
      (messageId, sentAt) => clock.timeOf(messageId, sentAt, live)
    )
    const elapsed = perfNow() - started
    threadTranscriptBuilt(threadId, elapsed)
    runTranscriptBuilt(threadId, elapsed)
    return built
  }, [clock, live, stream.messages, stream.toolCalls, threadId])

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
  // A window counts rows, not turns. Back-to-back quiet turns (a wake-up and a
  // run that said nothing) fold into one row, so a stretch of them counts
  // once. Otherwise a monitor's wake-ups could fill the window and the thread
  // would open on one fold.
  const newestTurnsStart = (count: number, before: number) => {
    let rows = 0
    let end = before
    let newerQuiet = false
    for (const start of turnStarts
      .filter((index) => index < before)
      .reverse()) {
      const quiet = messages
        .slice(start, end)
        .every((message) => isWake(message) || isSilentRun(message))
      if (!(quiet && newerQuiet)) {
        if (rows === count) return end
        rows++
      }
      newerQuiet = quiet
      end = start
    }
    return 0
  }
  const pinned =
    firstRow === undefined
      ? -1
      : firstRow === null
        ? 0
        : messages.findIndex((message) => message.id === firstRow)
  const shownFrom =
    pinned >= 0
      ? pinned
      : newestTurnsStart(INITIAL_TURN_WINDOW, messages.length)
  const showFrom = (index: number) =>
    setFirstRow(index > 0 ? (messages[index]?.id ?? null) : null)
  if (pinned < 0 && !stream.isThreadLoading && messages.length > 0)
    showFrom(shownFrom)
  const loadOlder = () => showFrom(newestTurnsStart(TURN_PAGE_SIZE, shownFrom))
  const loadAllOlder = useCallback(() => setFirstRow(null), [])

  return {
    kind: "stream",
    stream,
    threadId,
    messages,
    shownFrom,
    queued,
    cancelQueued: cancelSubmission,
    settleQueue,
    forgetQueued,
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
