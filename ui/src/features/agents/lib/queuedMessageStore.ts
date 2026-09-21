import { create } from "zustand"

import type { ImageChunk, Message } from "@/features/agents/lib/types"

/**
 * A composer submission held back while the thread's run is live. It carries
 * the full draft so the send path can dispatch it later with the same text,
 * attachments, and model choice the user pressed Enter on.
 */
export interface QueuedComposerMessage {
  id: string
  text: string
  images: Array<ImageChunk>
  modelId: string | null
  effort: string | null
  planMode: boolean
  /**
   * The newest completed tool call at queue time. A different id later means
   * a tool call finished after the user queued, which is the boundary the
   * message goes out on.
   */
  queuedAfterToolCallId: string | null
  /**
   * Set when the message was created by Stop or a failed send, not by the user
   * pressing send. It waits for Send now instead of leaving on its own.
   */
  holdUntilUserAction?: boolean
  createdAt: number
}

interface QueuedMessageStoreState {
  queuesByThreadId: Record<string, Array<QueuedComposerMessage>>
  /**
   * Bumped by `drain`. A send that took a message before a drain and settles
   * after it compares this to the value it captured and gives up, so Stop
   * cannot be followed by a queued message starting a new run.
   */
  drainGeneration: number
  enqueue: (
    threadId: string,
    message: Omit<QueuedComposerMessage, "id">
  ) => QueuedComposerMessage
  /**
   * Removes one message and returns it, or null when another caller already
   * took it. The remaining messages are re-anchored to `toolCallId` so only
   * one queued message leaves per tool boundary.
   */
  take: (
    threadId: string,
    id: string,
    toolCallId: string | null
  ) => QueuedComposerMessage | null
  /** Removes one message without touching the others' anchors. Null when already gone. */
  remove: (threadId: string, id: string) => QueuedComposerMessage | null
  /**
   * Puts a message back at the head, held for user action. Used when its send
   * failed: the queue keeps its order and nothing behind it overtakes.
   */
  holdAtFront: (threadId: string, message: QueuedComposerMessage) => void
  /** Removes and returns every queued message for the thread, oldest first. */
  drain: (threadId: string) => Array<QueuedComposerMessage>
}

const EMPTY_QUEUE: Array<QueuedComposerMessage> = []

function withoutThread(
  queues: Record<string, Array<QueuedComposerMessage>>,
  threadId: string,
  remaining: Array<QueuedComposerMessage>
): Record<string, Array<QueuedComposerMessage>> {
  const next = { ...queues }
  if (remaining.length === 0) delete next[threadId]
  else next[threadId] = remaining
  return next
}

/** In-memory only: a queued message is a live intent, not a draft worth persisting. */
export const useQueuedMessageStore = create<QueuedMessageStoreState>()(
  (set, get) => ({
    queuesByThreadId: {},
    drainGeneration: 0,
    enqueue: (threadId, message) => {
      const entry: QueuedComposerMessage = {
        ...message,
        id: crypto.randomUUID(),
      }
      set((state) => ({
        queuesByThreadId: {
          ...state.queuesByThreadId,
          [threadId]: [
            ...(state.queuesByThreadId[threadId] ?? EMPTY_QUEUE),
            entry,
          ],
        },
      }))
      return entry
    },
    take: (threadId, id, toolCallId) => {
      const queue = get().queuesByThreadId[threadId]
      const entry = queue?.find((message) => message.id === id)
      if (!queue || !entry) return null
      set((state) => ({
        queuesByThreadId: withoutThread(
          state.queuesByThreadId,
          threadId,
          (state.queuesByThreadId[threadId] ?? EMPTY_QUEUE)
            .filter((message) => message.id !== id)
            .map((message) =>
              message.queuedAfterToolCallId === toolCallId
                ? message
                : { ...message, queuedAfterToolCallId: toolCallId }
            )
        ),
      }))
      return entry
    },
    remove: (threadId, id) => {
      const queue = get().queuesByThreadId[threadId]
      const entry = queue?.find((message) => message.id === id)
      if (!queue || !entry) return null
      set((state) => ({
        queuesByThreadId: withoutThread(
          state.queuesByThreadId,
          threadId,
          (state.queuesByThreadId[threadId] ?? EMPTY_QUEUE).filter(
            (message) => message.id !== id
          )
        ),
      }))
      return entry
    },
    holdAtFront: (threadId, message) => {
      set((state) => {
        const rest = (state.queuesByThreadId[threadId] ?? EMPTY_QUEUE).filter(
          (entry) => entry.id !== message.id
        )
        return {
          queuesByThreadId: {
            ...state.queuesByThreadId,
            [threadId]: [{ ...message, holdUntilUserAction: true }, ...rest],
          },
        }
      })
    },
    drain: (threadId) => {
      const queue = get().queuesByThreadId[threadId]
      if (!queue || queue.length === 0) return EMPTY_QUEUE
      set((state) => ({
        queuesByThreadId: withoutThread(state.queuesByThreadId, threadId, []),
        drainGeneration: state.drainGeneration + 1,
      }))
      return queue
    },
  })
)

/**
 * The newest finished tool call, the id whose change is the boundary a queued
 * message goes out on. Messages and their chunks arrive in order, so the last
 * settled call wins.
 */
export function latestCompletedToolCallId(
  messages: ReadonlyArray<Message>
): string | null {
  let latest: string | null = null
  for (const message of messages) {
    for (const chunk of message.chunks) {
      if (chunk.kind !== "tool-execution") continue
      if (chunk.status !== "completed" && chunk.status !== "error") continue
      latest = chunk.toolCallId
    }
  }
  return latest
}

export type QueuePhase = "connecting" | "running" | "ready"

/**
 * A queued message is due mid-run once a tool call finished after it was
 * queued, and as soon as the run is over otherwise. "connecting" is the gap
 * between a send and the server picking it up, so nothing is due there.
 */
export function isQueuedMessageDue(input: {
  message: Pick<
    QueuedComposerMessage,
    "queuedAfterToolCallId" | "holdUntilUserAction"
  >
  phase: QueuePhase
  latestToolCallId: string | null
}): boolean {
  if (input.message.holdUntilUserAction) return false
  if (input.phase === "connecting") return false
  if (input.phase !== "running") return true
  return input.latestToolCallId !== input.message.queuedAfterToolCallId
}

export function useQueuedMessages(
  threadId: string
): Array<QueuedComposerMessage> {
  return useQueuedMessageStore(
    (state) => state.queuesByThreadId[threadId] ?? EMPTY_QUEUE
  )
}
