import { ChatToolCallReasoning } from "@langchain/macaw-components/ChatToolCall"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Thought } from "./thought"

export function ReasoningBlock({
  text,
  isLive,
}: {
  text: string
  isLive: boolean
}) {
  const thought = new Thought(text)
  if (!thought.text && !isLive) return null
  const label = isLive
    ? thought.liveLabel
      ? `${thought.liveLabel}...`
      : "Thinking..."
    : `Thought ${thought.preview || "Reasoning"}`
  const body = isLive ? thought.liveBody : thought.bodyUnder(thought.preview)
  return (
    <ChatToolCallReasoning
      label={label}
      status={isLive ? "streaming" : "completed"}
      expandWhileStreaming
    >
      {body ? (
        <Markdown
          content={body}
          isLive={isLive}
          className="text-xs leading-5 text-secondary"
        />
      ) : null}
    </ChatToolCallReasoning>
  )
}
