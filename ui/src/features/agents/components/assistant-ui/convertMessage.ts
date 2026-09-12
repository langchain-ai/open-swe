import type { ThreadMessageLike } from "@assistant-ui/react"
import type { Chunk, Message } from "@/features/agents/lib/types"

type MessagePart = Exclude<ThreadMessageLike["content"], string>[number]

function convertChunk(chunk: Chunk): MessagePart {
  switch (chunk.kind) {
    case "text":
    case "reasoning":
      return { type: chunk.kind, text: chunk.text }
    case "image":
      return {
        type: "image",
        image: `data:${chunk.mimeType};base64,${chunk.base64}`,
        filename: chunk.fileName,
      }
    case "tool-execution":
      return {
        type: "tool-call",
        toolCallId: chunk.toolCallId,
        toolName: chunk.title,
        artifact: chunk,
        argsText: JSON.stringify(chunk.input ?? {}),
        ...(chunk.status === "completed" || chunk.status === "error"
          ? { result: chunk.output ?? "", isError: chunk.status === "error" }
          : {}),
      }
    case "code":
      return {
        type: "text",
        text: `\`\`\`${chunk.language ?? ""}\n${chunk.text}\n\`\`\``,
      }
    case "error":
      return { type: "data", name: "error", data: chunk.text }
    case "list":
      return {
        type: "text",
        text: chunk.lines.map((line) => `- ${line}`).join("\n"),
      }
    case "todo":
      return { type: "data", name: "todos", data: chunk.todos }
  }
}

export function convertMessage(message: Message): ThreadMessageLike {
  const timestamp = Date.parse(message.timestamp)
  return {
    id: message.id,
    role:
      message.author === "user"
        ? "user"
        : message.author === "system"
          ? "system"
          : "assistant",
    ...(Number.isFinite(timestamp) ? { createdAt: new Date(timestamp) } : {}),
    content: message.chunks.map(convertChunk),
    // Keep application-only data (diffs, approvals, sender identity) for our renderers.
    metadata: { custom: { source: message } },
  }
}
