import { ExternalLink } from "lucide-react"
import { Link } from "@tanstack/react-router"

import { Button } from "@/components/ui/button"
import { ExpandableMessageChip } from "./ExpandableMessageChip"

import { MessageTimestamp } from "./MessageTimestamp"
import type { TaskEventMetadata } from "@/features/agents/lib/structuredInputMessages"

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
  const label =
    event.sender_label ??
    `${event.sender_role === "worker" ? "Worker" : "Coordinator"} ${event.sender_thread_id.slice(0, 8)}`
  const status =
    event.kind === "message" ? "Message" : COMPLETION_LABELS[event.status]

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
          <Button
            variant="ghost"
            size="icon-xs"
            nativeButton={false}
            role="link"
            render={
              <Link
                to="/agents/$threadId"
                params={{ threadId: event.sender_thread_id }}
              />
            }
            aria-label={`Open ${label} thread`}
            title={`Open ${label} thread`}
          >
            <ExternalLink className="size-3" />
          </Button>
        }
      >
        <div className="mt-1 max-w-full rounded-xl border border-default bg-surface-level-2/50 p-3">
          <div className="text-[13px] leading-relaxed break-words whitespace-pre-wrap text-primary">
            {event.content}
          </div>
          {timestamp && (
            <MessageTimestamp timestamp={timestamp} className="mt-2" />
          )}
        </div>
      </ExpandableMessageChip>
    </div>
  )
}
