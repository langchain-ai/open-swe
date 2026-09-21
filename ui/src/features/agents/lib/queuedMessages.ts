import type {
  AnyImageChunk,
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

/**
 * Images as the composer holds them. A queued message's images are served by
 * the transcript, so putting them back in the composer means fetching the bytes
 * again; one that cannot be fetched is dropped rather than failing the rest.
 */
export async function materializeImages(
  images: ReadonlyArray<AnyImageChunk>
): Promise<Array<ImageChunk>> {
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
  return settled.filter((image): image is ImageChunk => image !== null)
}
