import { ExternalLink } from "lucide-react"
import { Link } from "@tanstack/react-router"

import { Button } from "@/components/ui/button"
import { ExpandableMessageChip } from "./ExpandableMessageChip"

import { MessageTimestamp } from "./MessageTimestamp"
import type { TaskEventMetadata } from "@/features/agents/lib/structuredInputMessages"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

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
  return (
    <TaskMessageChip
      label={
        event.sender_label ??
        `${event.sender_role === "worker" ? "Worker" : "Coordinator"} ${event.sender_thread_id.slice(0, 8)}`
      }
      status={
        event.kind === "message" ? "Message" : COMPLETION_LABELS[event.status]
      }
      threadId={event.sender_thread_id}
      content={event.content}
      messageId={messageId}
      timestamp={timestamp}
    />
  )
}

export function WorkerSpawnMessage({ chunk }: { chunk: ToolExecutionChunk }) {
  let result: unknown
  try {
    result = JSON.parse(chunk.output ?? "null")
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
  }
  const success =
    result !== null && typeof result === "object" && "success" in result
      ? result.success
      : undefined
  const threadId =
    success === true &&
    result !== null &&
    typeof result === "object" &&
    "worker_thread_id" in result &&
    typeof result.worker_thread_id === "string" &&
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(
      result.worker_thread_id
    )
      ? result.worker_thread_id
      : undefined
  const status =
    chunk.status === "error" || success === false
      ? "Launch failed"
      : chunk.status === "pending" || chunk.status === "in_progress"
        ? "Starting…"
        : threadId
          ? "Started"
          : "Launch status unknown"
  return (
    <TaskMessageChip
      label={threadId ? `Worker ${threadId.slice(0, 8)}` : "Worker"}
      status={status}
      threadId={status === "Started" ? threadId : undefined}
      content={
        typeof chunk.input?.instructions === "string"
          ? chunk.input.instructions
          : "Worker assignment unavailable."
      }
      messageId={chunk.toolCallId}
      timestamp={chunk.timestamp}
    />
  )
}

function TaskMessageChip({
  label,
  status,
  threadId,
  content,
  messageId,
  timestamp,
}: {
  label: string
  status: string
  threadId?: string
  content: string
  messageId: string
  timestamp?: string
}) {
  return (
    <div
      className="my-3 flex flex-col items-start gap-1"
      data-testid="task-event"
      data-message-id={messageId}
    >
      <ExpandableMessageChip
        accessibleLabel={`${label} · ${status}`}
        label={
          <>
            <span className="truncate" title={label}>
              {label}
            </span>
            <span className="shrink-0">{` · ${status}`}</span>
          </>
        }
        actions={
          threadId && (
            <Button
              variant="ghost"
              size="icon-xs"
              nativeButton={false}
              role="link"
              render={<Link to="/agents/$threadId" params={{ threadId }} />}
              aria-label={`Open ${label} thread`}
              title={`Open ${label} thread`}
            >
              <ExternalLink className="size-3" />
            </Button>
          )
        }
      >
        <div className="mt-1 max-w-full rounded-xl border border-border bg-muted/50 p-3">
          <div className="text-[13px] leading-relaxed break-words whitespace-pre-wrap text-foreground">
            {content}
          </div>
          {timestamp && (
            <MessageTimestamp timestamp={timestamp} className="mt-2" />
          )}
        </div>
      </ExpandableMessageChip>
    </div>
  )
}
