import { useAgentStream } from "@/features/agents/lib/stream/AgentStreamProvider"
import { useMemo, useState } from "react"
import {
  ReadonlyThreadProvider,
  ThreadPrimitive,
  fromThreadMessageLike,
} from "@assistant-ui/react"
import { useMessages, useToolCalls } from "@langchain/react"
import { Bot, ChevronDown, Loader2 } from "lucide-react"

import { convertMessage } from "./convertMessage"
import { AssistantMessage } from "./AssistantMessage"
import { Markdown } from "../chat/Markdown"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { messageArrivalTimestamp } from "@/features/agents/lib/messageTimestamps"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"

function SubagentResult({ chunk }: { chunk: ToolExecutionChunk }) {
  return chunk.output ? (
    <Markdown content={chunk.output} />
  ) : (
    <p className="text-xs text-muted-foreground">
      {chunk.status === "error"
        ? "This subagent failed."
        : chunk.status === "completed"
          ? "No subagent transcript is available."
          : "Waiting for subagent activity…"}
    </p>
  )
}

function ScopedConversation({
  namespace,
  chunk,
}: {
  namespace: readonly string[]
  chunk: ToolExecutionChunk
}) {
  const stream = useAgentStream()
  const messages = useMessages(stream, { namespace })
  const toolCalls = useToolCalls(stream, { namespace })
  const uiMessages = useMemo(
    () =>
      streamMessagesToUi(messages, toolCalls, messageArrivalTimestamp).filter(
        (message) => !message.hidden
      ),
    [messages, toolCalls]
  )
  const running = chunk.status === "pending" || chunk.status === "in_progress"
  const nestedMessages = useMemo(
    () =>
      uiMessages.map((message, index) =>
        fromThreadMessageLike(
          convertMessage(message),
          message.id,
          running && index === uiMessages.length - 1
            ? { type: "running" }
            : { type: "complete", reason: "stop" }
        )
      ),
    [running, uiMessages]
  )

  if (!nestedMessages.length) return <SubagentResult chunk={chunk} />

  return (
    <ReadonlyThreadProvider messages={nestedMessages}>
      <ThreadPrimitive.Messages>
        {() => <AssistantMessage />}
      </ThreadPrimitive.Messages>
    </ReadonlyThreadProvider>
  )
}

function DiscoveredConversation({ chunk }: { chunk: ToolExecutionChunk }) {
  const stream = useAgentStream()
  const discovery = stream.subagents.get(chunk.toolCallId)
  const namespace = discovery?.namespace ?? chunk.subagentNamespace
  return namespace?.length ? (
    <ScopedConversation
      key={namespace.join("|")}
      namespace={namespace}
      chunk={chunk}
    />
  ) : (
    <SubagentResult chunk={chunk} />
  )
}

function AssistantSubagent({ chunk }: { chunk: ToolExecutionChunk }) {
  const [expanded, setExpanded] = useState(false)
  const inStream = useIsInAgentThreadStream()
  const name =
    typeof chunk.input?.subagent_type === "string"
      ? chunk.input.subagent_type
      : "Subagent"
  const description =
    typeof chunk.input?.description === "string" ? chunk.input.description : ""
  const running = chunk.status === "pending" || chunk.status === "in_progress"
  return (
    <div className="min-w-0 rounded-xl border border-border bg-card">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 p-3 text-left text-xs focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        {running ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin" />
        ) : (
          <Bot className="size-3.5 shrink-0" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block font-medium">{name}</span>
          {description && (
            <span className="mt-1 block truncate text-muted-foreground">
              {description}
            </span>
          )}
        </span>
        <span
          className={
            chunk.status === "error"
              ? "text-destructive"
              : "text-muted-foreground"
          }
        >
          {running
            ? "Working"
            : chunk.status === "error"
              ? "Failed"
              : "Completed"}
        </span>
        <ChevronDown
          className={`size-3.5 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && (
        <div
          className="max-h-96 overflow-auto border-t border-border px-3 py-2"
          aria-label={`${name} conversation`}
        >
          {inStream ? (
            <DiscoveredConversation chunk={chunk} />
          ) : (
            <SubagentResult chunk={chunk} />
          )}
        </div>
      )}
    </div>
  )
}

export function AssistantSubagents({
  chunks,
}: {
  chunks: Array<ToolExecutionChunk>
}) {
  return (
    <div className="space-y-2">
      {chunks.map((chunk) => (
        <AssistantSubagent key={chunk.toolCallId} chunk={chunk} />
      ))}
    </div>
  )
}
