import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react"
import { useState } from "react"

import { MessageTimestamp } from "./MessageTimestamp"
import type { TaskEventMetadata } from "@/features/agents/lib/structuredInputMessages"

const GENERIC_LABELS = new Set([
  "untitled",
  "untitled thread",
  "new thread",
  "new chat",
  "new conversation",
  "untitled conversation",
  "worker",
  "coordinator",
])

const COMPLETION_LABELS = {
  success: "Completed",
  error: "Failed",
  timeout: "Timed out",
  interrupted: "Interrupted",
} satisfies Record<
  Extract<TaskEventMetadata, { kind: "completion" }>["status"],
  string
>

export function TaskEventMessage({
  event,
  messageId,
  timestamp,
}: {
  event: TaskEventMetadata
  messageId: string
  timestamp?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const providedLabel = event.sender_label?.trim()
  const label =
    providedLabel && !GENERIC_LABELS.has(providedLabel.toLowerCase())
      ? providedLabel
      : `${event.sender_role === "worker" ? "Worker" : "Coordinator"} ${event.sender_thread_id.slice(0, 8)}`
  const status =
    event.kind === "message" ? "Message" : COMPLETION_LABELS[event.status]

  return (
    <div
      className="my-3 flex flex-col items-start gap-1"
      data-testid="task-event"
      data-message-id={messageId}
    >
      <div className="flex max-w-full items-center gap-1.5">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-label={`${label} · ${status}`}
          className="flex min-w-0 items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-accent/30"
        >
          {expanded ? (
            <ChevronDown className="size-3 shrink-0" />
          ) : (
            <ChevronRight className="size-3 shrink-0" />
          )}
          <span className="truncate" title={label}>
            {label}
          </span>
          <span className="shrink-0">{` · ${status}`}</span>
        </button>
        <a
          href={`/agents/${event.sender_thread_id}`}
          aria-label={`Open ${label} thread`}
          title={`Open ${label} thread`}
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent/30 hover:text-foreground"
        >
          <ExternalLink className="size-3" />
        </a>
      </div>
      {expanded && (
        <div className="max-w-full rounded-xl border border-border bg-muted/50 p-3">
          <div className="text-[13px] leading-relaxed break-words whitespace-pre-wrap text-foreground">
            {event.content}
          </div>
          {timestamp && (
            <MessageTimestamp timestamp={timestamp} className="mt-2" />
          )}
        </div>
      )}
    </div>
  )
}
