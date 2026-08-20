import type { Message, QueuedThreadMessage } from "@/features/agents/lib/types"

function messageText(message: Message): string {
  return message.chunks
    .map((chunk) => (chunk.kind === "text" ? chunk.text : ""))
    .join("\n")
    .trim()
}

export function visibleQueuedMessages(
  queuedMessages: Array<QueuedThreadMessage> | undefined,
  messages: Array<Message>
): Array<QueuedThreadMessage> {
  const queued = queuedMessages ?? []
  if (queued.length === 0) return queued

  const userMessages = messages
    .filter((message) => message.author === "user")
    .map((message) => ({
      text: messageText(message),
      timestamp: Date.parse(message.timestamp),
      timestampIsFallback: message.timestampIsFallback,
      consumed: false,
    }))

  return queued.filter((queuedMessage) => {
    const queuedText = queuedMessage.content.trim()
    if (!queuedText) return true

    const match = userMessages.find((message) => {
      if (message.consumed || !message.text.includes(queuedText)) return false
      if (message.timestampIsFallback || !Number.isFinite(message.timestamp))
        return true
      return message.timestamp >= queuedMessage.createdAt - 1000
    })
    if (!match) return true

    match.consumed = true
    return false
  })
}
