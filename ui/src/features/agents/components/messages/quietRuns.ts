import {
  buildRenderItems,
  collapsedWorkItems,
  countWorkActions,
  segmentTurn,
} from "./renderItems"
import type { Message } from "@/features/agents/lib/types"

/**
 * Consecutive runs that ended without a word to the reader, such as a monitor
 * or event listener waking the agent to check on something that had not
 * changed. Each one alone renders a trigger chip and a "Worked" fold row; a
 * long wait stacks dozens of them, so the transcript shows them as one row.
 */
export type QuietStretch = {
  key: string
  messages: Array<Message>
  runCount: number
  actionCount: number
  /** Summed run time, when every run's timestamps are real. */
  durationMs: number | null
  /** What woke the agent, most frequent first, as [name, count]. */
  triggers: Array<[string, number]>
}

export type TranscriptEntry =
  | { type: "message"; message: Message; index: number }
  | { type: "quiet"; stretch: QuietStretch }

/** A stretch needs this many silent runs before it folds into one row. */
const MIN_QUIET_RUNS = 2

/**
 * Senders whose messages wake the agent to check on something. Other system
 * messages, such as a sandbox move notice, say something the reader should see.
 */
const WAKE_SENDERS = new Set([
  "system:monitor",
  "system:event-subscription",
  "system:thread-wakeup",
  "system:background-event",
  "system:background-task",
  "system:goal",
])

function isWake(message: Message): boolean {
  return (
    message.structuredSenderKind === "system" &&
    !message.taskEvent &&
    WAKE_SENDERS.has(message.structuredSenderId ?? "")
  )
}

/** A settled turn without a reply or failed, pending, or unfinished calls. */
export function isSilentRun(message: Message): boolean {
  if (message.author !== "agent" || message.structuredSenderKind === "system")
    return false
  if (message.chunks.length === 0) return false
  const calls = message.chunks.filter(
    (chunk) => chunk.kind === "tool-execution"
  )
  for (const call of calls) {
    if (call.status === "error") return false
    calls.push(...(call.calls ?? []))
  }
  return segmentTurn(buildRenderItems(message.chunks, message.id)).every(
    (segment) =>
      segment.type === "work" &&
      collapsedWorkItems(segment.items, true).length === 0
  )
}

function summarize(first: Message, messages: Array<Message>): QuietStretch {
  let actionCount = 0
  let runCount = 0
  let durationMs: number | null = 0
  const triggers = new Map<string, number>()
  for (const message of messages) {
    if (isWake(message)) {
      const name = message.structuredSenderName || "Context"
      triggers.set(name, (triggers.get(name) ?? 0) + 1)
      continue
    }
    runCount += 1
    actionCount += countWorkActions(
      buildRenderItems(message.chunks, message.id)
    )
    const start = message.startedAt ? Date.parse(message.startedAt) : NaN
    const end = Date.parse(message.timestamp)
    durationMs =
      durationMs !== null &&
      !message.timestampIsFallback &&
      Number.isFinite(start) &&
      Number.isFinite(end) &&
      end >= start
        ? durationMs + (end - start)
        : null
  }
  return {
    key: `quiet-${first.id}`,
    messages,
    runCount,
    actionCount,
    durationMs,
    triggers: [...triggers].sort((a, b) => b[1] - a[1]),
  }
}

/**
 * Folds each stretch of silent runs, with the wake-ups that started them,
 * into one entry. A wake-up stays out when the run it started replied, so
 * the reply keeps its cause beside it; the live run is never folded.
 */
export function groupQuietRuns(
  messages: Array<Message>,
  liveMessageId?: string
): Array<TranscriptEntry> {
  const entries: Array<TranscriptEntry> = []
  let pending: Array<{ message: Message; index: number }> = []
  let stretch: Array<{ message: Message; index: number }> = []

  const flushStretch = () => {
    const runs = stretch.filter(({ message }) => !isWake(message))
    const first = stretch[0]?.message
    if (first && runs.length >= MIN_QUIET_RUNS) {
      entries.push({
        type: "quiet",
        stretch: summarize(
          first,
          stretch.map(({ message }) => message)
        ),
      })
    } else {
      for (const item of stretch) entries.push({ type: "message", ...item })
    }
    stretch = []
  }
  const flushPending = () => {
    for (const item of pending) entries.push({ type: "message", ...item })
    pending = []
  }

  messages.forEach((message, index) => {
    if (isWake(message)) {
      pending.push({ message, index })
      return
    }
    if (message.id !== liveMessageId && isSilentRun(message)) {
      stretch.push(...pending, { message, index })
      pending = []
      return
    }
    flushStretch()
    flushPending()
    entries.push({ type: "message", message, index })
  })
  flushStretch()
  flushPending()
  return entries
}
