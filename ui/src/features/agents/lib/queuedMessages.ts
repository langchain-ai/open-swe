import type { SubmissionQueueEntry } from "@langchain/react"

import type {
  ImageChunk,
  Message,
  PendingThreadMessage,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"

interface QueuedContentBlock {
  type?: string
  text?: string
  base64?: string
  mime_type?: string
  file_name?: string
}

interface QueuedSubmitValues {
  messages?: Array<{ id?: string; content?: string | Array<QueuedContentBlock> }>
}

function messageIds(messages: Array<Message>): Set<string> {
  return new Set(messages.map((message) => message.id))
}

export function visiblePendingMessages(
  pendingMessages: Array<PendingThreadMessage> | undefined,
  messages: Array<Message>,
  queuedMessages: Array<QueuedThreadMessage> = []
): Array<Message> {
  const persistedIds = messageIds(messages)
  const queuedIds = new Set(queuedMessages.map((message) => message.id))
  return (pendingMessages ?? [])
    .filter(
      (message) => !persistedIds.has(message.id) && !queuedIds.has(message.id)
    )
    .map((message) => ({
      id: message.id,
      author: "user",
      timestamp: new Date(message.createdAt).toISOString(),
      timestampIsFallback: true,
      deliveryStatus: message.status,
      optimistic: true,
      chunks: [
        ...(message.images ?? []),
        ...(message.content
          ? [{ kind: "text" as const, text: message.content }]
          : []),
      ],
    }))
}

/** Reconstructs the composer display shape from a submission queue entry's raw `submit()` values. */
export function queueEntryToMessage(
  entry: SubmissionQueueEntry
): QueuedThreadMessage | null {
  // The *last* message, not the first: a locally-dispatched entry carries
  // just the one message the composer sent, but an entry rehydrated from the
  // server (a queued run created by another session) carries the full,
  // attributed message list `build_input_messages` builds — dynamic-context
  // preambles first, the real user message last. Every backend reader
  // (`_command_message_content`, `_command_message_id`) treats the last
  // message as the user's content for the same reason; the preamble
  // messages also lack an `id`, so reading index 0 here silently dropped
  // every rehydrated queued message (it has no id to key off of).
  const messages = (entry.values as QueuedSubmitValues | null | undefined)
    ?.messages
  const message = messages?.[messages.length - 1]
  if (!message?.id) return null
  if (typeof message.content === "string") {
    return {
      id: message.id,
      content: message.content,
      images: [],
      createdAt: entry.createdAt.getTime(),
    }
  }
  const blocks = Array.isArray(message.content) ? message.content : []
  const images: Array<ImageChunk> = blocks.flatMap((block) =>
    block.type === "image" && block.base64 && block.mime_type
      ? [
          {
            kind: "image" as const,
            base64: block.base64,
            mimeType: block.mime_type,
            ...(block.file_name ? { fileName: block.file_name } : {}),
          },
        ]
      : []
  )
  const content = blocks
    .filter((block) => block.type === "text" && block.text)
    .map((block) => block.text)
    .join("\n\n")
  return { id: message.id, content, images, createdAt: entry.createdAt.getTime() }
}
