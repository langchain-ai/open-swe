import type { SubmissionQueueEntry } from "@langchain/react"

import type { QueuedTurn } from "@/features/agents/lib/transcript/reducer"
import type {
  AnyImageChunk,
  Chunk,
  ImageChunk,
  Message,
  PendingThreadMessage,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"

function messageText(message: Message): string {
  return message.chunks
    .map((chunk) => (chunk.kind === "text" ? chunk.text : ""))
    .join("\n")
    .trim()
}

function messageIds(messages: Array<Message>): Set<string> {
  return new Set(messages.map((message) => message.id))
}

export function visiblePendingMessages(
  pendingMessages: Array<PendingThreadMessage> | undefined,
  messages: Array<Message>
): Array<Message> {
  const persistedIds = messageIds(messages)
  return (pendingMessages ?? [])
    .filter((message) => !persistedIds.has(message.id))
    .map((message) => ({
      id: message.id,
      author: "user",
      timestamp: new Date(message.createdAt).toISOString(),
      timestampIsFallback: true,
      deliveryStatus: message.status,
      deliveryError: message.error,
      optimistic: true,
      chunks: [
        ...(message.images ?? []),
        ...(message.content
          ? [{ kind: "text" as const, text: message.content }]
          : []),
      ],
    }))
}

export function visibleQueuedMessages(
  queuedMessages: Array<QueuedThreadMessage> | undefined,
  messages: Array<Message>
): Array<QueuedThreadMessage> {
  const queued = queuedMessages ?? []
  if (queued.length === 0) return queued

  const persistedIds = messageIds(messages)
  const userMessages = messages
    .filter((message) => message.author === "user")
    .map((message) => ({
      text: messageText(message),
      timestamp: Date.parse(message.timestamp),
      consumed: false,
    }))

  return queued.filter((queuedMessage) => {
    if (persistedIds.has(queuedMessage.id)) return false
    if (!queuedMessage.id.startsWith("queued-")) return true
    const queuedText = queuedMessage.content.trim()
    if (!queuedText) return true

    const match = userMessages.find((message) => {
      if (message.consumed || !message.text.includes(queuedText)) return false
      if (!Number.isFinite(message.timestamp)) return true
      return message.timestamp >= queuedMessage.createdAt - 1000
    })
    if (!match) return true

    match.consumed = true
    return false
  })
}

interface QueuedSubmitContentBlock {
  type?: string
  text?: string
  base64?: string
  mime_type?: string
  file_name?: string
}
interface QueuedSubmitMessage {
  id?: string
  content?: string | Array<QueuedSubmitContentBlock>
}
interface QueuedSubmitValues {
  messages?: Array<QueuedSubmitMessage>
}

/**
 * Chunks of a queue entry's submitted message. Reads the *last* message in
 * `values.messages`, not the first — a hydrated entry's list is dynamic-
 * context preambles followed by the real user message.
 */
function queuedEntryChunks(
  entry: SubmissionQueueEntry
): { id: string; chunks: Array<Chunk> } | null {
  const messages = (entry.values as QueuedSubmitValues | null | undefined)
    ?.messages
  const raw = messages?.[messages.length - 1]
  if (!raw?.id) return null

  const chunks: Array<Chunk> = []
  if (typeof raw.content === "string") {
    if (raw.content) chunks.push({ kind: "text", text: raw.content })
  } else if (Array.isArray(raw.content)) {
    for (const block of raw.content) {
      if (block.type === "image" && block.base64 && block.mime_type) {
        chunks.push({
          kind: "image",
          base64: block.base64,
          mimeType: block.mime_type,
          ...(block.file_name ? { fileName: block.file_name } : {}),
        })
      } else if (block.type === "text" && block.text) {
        chunks.push({ kind: "text", text: block.text })
      }
    }
  }
  return { id: raw.id, chunks }
}

/**
 * Reconstructs a `QueuedTurn` from the SDK's own submission-queue entry
 * (for "stream"-kind threads, which have no transcript). The SDK carries no
 * sender attribution, so `senderLogin` here is always the current viewer —
 * the run's own `queued_by` metadata is the real cancel-authorization gate.
 */
export function queueEntryToTurn(
  entry: SubmissionQueueEntry,
  login: string | undefined
): QueuedTurn | null {
  const found = queuedEntryChunks(entry)
  if (!found) return null
  const { id, chunks } = found

  return {
    turnId: entry.id,
    runId: entry.runId ?? null,
    message: {
      id,
      author: "user",
      timestamp: entry.createdAt.toISOString(),
      chunks,
    },
    senderLogin: login ?? null,
    requestedAt: entry.createdAt.toISOString(),
  }
}

/**
 * Reconstructs the composer display shape from a submission queue entry, for
 * local/desktop threads (no `ThreadSource`/transcript to route it through).
 */
export function queueEntryToMessage(
  entry: SubmissionQueueEntry
): QueuedThreadMessage | null {
  const found = queuedEntryChunks(entry)
  if (!found) return null
  const { id, chunks } = found
  const images = chunks.filter(
    (chunk): chunk is ImageChunk => chunk.kind === "image"
  )
  const content = chunks
    .filter((chunk) => chunk.kind === "text")
    .map((chunk) => chunk.text)
    .join("\n\n")
  return { id, content, images, createdAt: entry.createdAt.getTime() }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error)
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : ""
      resolve(result.slice(result.indexOf(",") + 1))
    }
    reader.readAsDataURL(blob)
  })
}

export interface MaterializedImages {
  images: Array<ImageChunk>
  /** Images whose bytes could not be fetched; they are not in `images`. */
  failed: number
}

/**
 * Images as the composer holds them. A queued message's images are served by
 * the transcript, so putting them back in the composer means fetching the bytes
 * again. Callers decide what a failed fetch means: it is reported, not dropped
 * silently, because the queued run these images belong to may be about to go.
 */
export async function materializeImages(
  images: ReadonlyArray<AnyImageChunk>
): Promise<MaterializedImages> {
  const settled = await Promise.all(
    images.map(async (image): Promise<ImageChunk | null> => {
      if ("base64" in image) return image
      try {
        const response = await fetch(image.url, {
          credentials: image.credentials === "session" ? "include" : "omit",
        })
        if (!response.ok) return null
        const blob = await response.blob()
        return {
          kind: "image",
          base64: await blobToBase64(blob),
          mimeType: image.mimeType ?? blob.type,
          ...(image.fileName ? { fileName: image.fileName } : {}),
        }
      } catch {
        return null
      }
    })
  )
  const materialized = settled.filter(
    (image): image is ImageChunk => image !== null
  )
  return { images: materialized, failed: settled.length - materialized.length }
}
