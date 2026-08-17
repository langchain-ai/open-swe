import type { DesktopAcpEvent } from "@/desktop"
import type {
  AcpToolKind,
  AcpToolStatus,
  Chunk,
  ImageChunk,
  Message,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"

const TOOL_KINDS = new Set<AcpToolKind>([
  "read",
  "edit",
  "delete",
  "move",
  "search",
  "execute",
  "think",
  "fetch",
  "task",
  "other",
])
const TOOL_STATUSES = new Set<AcpToolStatus>([
  "pending",
  "in_progress",
  "completed",
  "error",
])

function eventText(event: DesktopAcpEvent, key: string): string {
  return typeof event[key] === "string" ? event[key] : ""
}

function currentAgentMessage(
  messages: Array<Message>,
  event: DesktopAcpEvent
): Message {
  const last = messages[messages.length - 1]
  if (last?.author === "agent") return last
  const message: Message = {
    id: `local-agent-${event.sequence}`,
    author: "agent",
    timestamp: event.timestamp,
    chunks: [],
  }
  messages.push(message)
  return message
}

function appendTextChunk(
  message: Message,
  kind: "text" | "reasoning",
  text: string
) {
  if (!text) return
  const last = message.chunks[message.chunks.length - 1]
  if (last?.kind === kind) last.text += text
  else message.chunks.push({ kind, text })
}

function toolChunk(event: DesktopAcpEvent): ToolExecutionChunk | null {
  const tool = event.tool
  if (!tool || typeof tool !== "object" || Array.isArray(tool)) return null
  const value = tool as Record<string, unknown>
  if (typeof value.toolCallId !== "string") return null
  const kind = typeof value.toolKind === "string" ? value.toolKind : "other"
  const status = typeof value.status === "string" ? value.status : "in_progress"
  return {
    kind: "tool-execution",
    toolCallId: value.toolCallId,
    title: typeof value.title === "string" ? value.title : "Tool",
    toolKind: TOOL_KINDS.has(kind as AcpToolKind)
      ? (kind as AcpToolKind)
      : "other",
    input:
      value.input &&
      typeof value.input === "object" &&
      !Array.isArray(value.input)
        ? (value.input as Record<string, unknown>)
        : {},
    status: TOOL_STATUSES.has(status as AcpToolStatus)
      ? (status as AcpToolStatus)
      : "in_progress",
    output: typeof value.output === "string" ? value.output : undefined,
    locations: Array.isArray(value.locations)
      ? (value.locations as ToolExecutionChunk["locations"])
      : undefined,
  }
}

export function desktopAcpMessages(
  events: Array<DesktopAcpEvent>
): Array<Message> {
  const messages: Array<Message> = []
  for (const event of events) {
    if (event.type === "user-message") {
      const chunks: Array<Chunk> = []
      const text = eventText(event, "text")
      if (text) chunks.push({ kind: "text", text })
      if (Array.isArray(event.images)) {
        chunks.push(...(event.images as Array<ImageChunk>))
      }
      messages.push({
        id: `local-user-${event.sequence}`,
        author: "user",
        timestamp: event.timestamp,
        chunks,
      })
      continue
    }
    if (event.type === "agent-text" || event.type === "agent-reasoning") {
      appendTextChunk(
        currentAgentMessage(messages, event),
        event.type === "agent-text" ? "text" : "reasoning",
        eventText(event, "text")
      )
      continue
    }
    if (event.type === "tool") {
      const chunk = toolChunk(event)
      if (!chunk) continue
      const message = currentAgentMessage(messages, event)
      const index = message.chunks.findIndex(
        (item) =>
          item.kind === "tool-execution" && item.toolCallId === chunk.toolCallId
      )
      if (index === -1) message.chunks.push(chunk)
      else message.chunks[index] = chunk
      continue
    }
    if (event.type === "error") {
      currentAgentMessage(messages, event).chunks.push({
        kind: "error",
        text:
          eventText(event, "message") ||
          "Deep Agents Code stopped unexpectedly",
      })
    }
  }
  return messages
}
