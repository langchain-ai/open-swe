import type { SubmissionQueueEntry } from "@langchain/react"

import { parseStructuredInput } from "@/features/agents/lib/structuredInputMessages"
import type { QueuedTurn } from "@/features/agents/lib/transcript/reducer"
import type { SessionUser } from "@/lib/api"
import type { PassageReply } from "@/features/agents/utils/passageReply"
import type {
  AnyImageChunk,
  Chunk,
  ImageChunk,
  Message,
  PendingThreadMessage,
} from "@/features/agents/lib/types"

function messageIds(messages: Array<Message>): Set<string> {
  return new Set(messages.map((message) => message.id))
}

/**
 * The label the server will give `login`'s next message: the one their earlier
 * messages carry, else their own display name, which labels a thread's first
 * message. A pending message shows it too, so the bubble doesn't drop when the
 * confirmed message replaces it. Undefined in threads without labels.
 */
export function pendingSenderName(
  messages: Array<Message>,
  login: string | undefined,
  displayName: string | undefined
): string | undefined {
  if (!login) return undefined
  const own = messages.findLast(
    (message) =>
      message.structuredSenderLogin?.toLowerCase() === login.toLowerCase()
  )
  if (own) return own.structuredSenderName
  if (messages.length === 0) return displayName
  return messages.some((message) => message.structuredSenderName)
    ? (displayName ?? `@${login}`)
    : undefined
}

export function visiblePendingMessages(
  pendingMessages: Array<PendingThreadMessage> | undefined,
  messages: Array<Message>,
  senderName?: string
): Array<Message> {
  const persistedIds = messageIds(messages)
  return (pendingMessages ?? [])
    .filter((message) => !persistedIds.has(message.id))
    .map((message) => ({
      id: message.id,
      author: "user",
      ...(senderName ? { structuredSenderName: senderName } : {}),
      timestamp: new Date(message.createdAt).toISOString(),
      // A sent message shows when it was sent, so its row keeps its height
      // when the transcript's own row takes over; until then the delivery
      // status holds that line.
      ...(message.status !== "sent"
        ? { timestampIsFallback: true, deliveryStatus: message.status }
        : {}),
      deliveryError: message.error,
      optimistic: true,
      ...(message.replies ? { replies: message.replies } : {}),
      chunks: [
        ...(message.images ?? []),
        ...(message.content
          ? [{ kind: "text" as const, text: message.content }]
          : []),
      ],
    }))
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
  type?: string
  role?: string
  content?: string | Array<QueuedSubmitContentBlock>
  additional_kwargs?: {
    replies?: Array<PassageReply>
    open_swe_provenance?: { sender?: string }
  }
}
interface QueuedSubmitValues {
  messages?: Array<QueuedSubmitMessage>
}

/**
 * Chunks of a queue entry's submitted message. Reads the *last* message in
 * `values.messages`, not the first: a hydrated entry's list is dynamic-context
 * preambles followed by the real user message. The backend built that message,
 * so its provenance names the sender and each of its text blocks wraps what the
 * sender wrote in an envelope. A message queued from this tab has neither.
 */
function queuedEntryChunks(entry: SubmissionQueueEntry): {
  id: string
  chunks: Array<Chunk>
  replies?: Array<PassageReply>
  sender?: string
} | null {
  const messages = (entry.values as QueuedSubmitValues | null | undefined)
    ?.messages
  const raw = messages?.[messages.length - 1]
  if (!raw?.id) return null
  // A plugin's tool results queue a run that ends in a tool message, which
  // nobody wrote and nobody should get back.
  const role = raw.type ?? raw.role
  if (role !== undefined && role !== "human" && role !== "user") return null

  const sender = raw.additional_kwargs?.open_swe_provenance?.sender
  let replies = raw.additional_kwargs?.replies
  const chunks: Array<Chunk> = []
  const blocks: Array<QueuedSubmitContentBlock> =
    typeof raw.content === "string"
      ? [{ type: "text", text: raw.content }]
      : Array.isArray(raw.content)
        ? raw.content
        : []
  for (const block of blocks) {
    if (block.type === "image" && block.base64 && block.mime_type) {
      chunks.push({
        kind: "image",
        base64: block.base64,
        mimeType: block.mime_type,
        ...(block.file_name ? { fileName: block.file_name } : {}),
      })
    } else if (block.type === "text" && block.text) {
      const parsed =
        sender === undefined ? null : parseStructuredInput(block.text)
      if (parsed?.type === "message") replies ??= parsed.replies
      const text = !parsed
        ? block.text
        : parsed.type === "entity"
          ? ""
          : parsed.content
      if (text) chunks.push({ kind: "text", text })
    }
  }
  return { id: raw.id, chunks, replies, sender }
}

/**
 * Reconstructs a `QueuedTurn` from the SDK's own submission-queue entry
 * (for "stream"-kind threads, which have no transcript). The SDK carries no
 * sender, so a message queued from this tab is the viewer's, and one the queue
 * rebuilt from the server's pending runs is theirs only when its provenance
 * names them. The run's own `queued_by` metadata is still the real
 * cancel-authorization gate.
 */
export function queueEntryToTurn(
  entry: SubmissionQueueEntry,
  viewer: Pick<SessionUser, "login" | "github_login" | "user_id"> | undefined
): QueuedTurn | null {
  const found = queuedEntryChunks(entry)
  if (!found) return null
  const { id, chunks, replies, sender } = found
  const viewerSenders = [
    viewer?.user_id && `user:${viewer.user_id}`,
    viewer && `github:${viewer.github_login ?? viewer.login}`,
  ]
  const own =
    sender === undefined ||
    viewerSenders.some(
      (candidate) =>
        candidate && candidate.toLowerCase() === sender.toLowerCase()
    )

  return {
    turnId: entry.id,
    runId: entry.runId ?? null,
    message: {
      id,
      author: "user",
      timestamp: entry.createdAt.toISOString(),
      chunks,
      ...(replies?.length ? { replies } : {}),
    },
    senderLogin: own ? (viewer?.login ?? null) : null,
    requestedAt: entry.createdAt.toISOString(),
  }
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
