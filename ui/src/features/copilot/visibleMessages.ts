import type { InputContent, Message } from "@ag-ui/client"
import {
  isSilentSender,
  parseStructuredInput,
} from "@/features/agents/lib/structuredInputMessages"

function textOf(parts: ReadonlyArray<InputContent>): string {
  return parts.map((part) => (part.type === "text" ? part.text : "")).join("")
}

/** What a person sees of the human turns: the authored text, without model-facing envelopes. */
export function visibleMessages(messages: Message[]): Message[] {
  return messages.flatMap((message): Message[] => {
    if (message.role !== "user") return [message]
    const parts: ReadonlyArray<InputContent> =
      typeof message.content === "string"
        ? [{ type: "text", text: message.content }]
        : message.content
    const parsed = parseStructuredInput(textOf(parts))
    if (parsed.type === "entity") return []
    if (parsed.type === "legacy") return [message]
    if (isSilentSender(parsed.sender)) return []
    const media = parts.filter((part) => part.type !== "text")
    return [
      {
        ...message,
        content: media.length
          ? [{ type: "text", text: parsed.content }, ...media]
          : parsed.content,
      },
    ]
  })
}
