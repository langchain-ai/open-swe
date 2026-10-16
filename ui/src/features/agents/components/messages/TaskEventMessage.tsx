import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Link } from "@tanstack/react-router"

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
      className="my-space-3 flex flex-col items-start gap-space-1"
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
          <IconButton
            asChild
            icon={ArrowSquareOutIcon}
            label={`Open ${label} thread`}
            size="xs"
            color="secondary"
            variant="plain"
          >
            <Link
              to="/agents/$threadId"
              params={{ threadId: event.sender_thread_id }}
            />
          </IconButton>
        }
      >
        <div className="mt-space-1 max-w-full rounded-xl border border-default bg-surface-level-2 p-space-3">
          <div className="text-xs leading-relaxed break-words whitespace-pre-wrap text-primary">
            {event.content}
          </div>
          {timestamp && (
            <MessageTimestamp timestamp={timestamp} className="mt-space-2" />
          )}
        </div>
      </ExpandableMessageChip>
    </div>
  )
}
