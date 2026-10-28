import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Link } from "@tanstack/react-router"

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
        `${event.sender_role === "worker" ? "Worker" : "Coordinator"} ${shortThreadId(event.sender_thread_id)}`
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

/** A thread id's random tail: thread ids are UUIDv7s, whose first characters are a timestamp shared by threads started together. */
function shortThreadId(threadId: string): string {
  return threadId.slice(-8)
}

const THREAD_ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

/** What a `start_async_task` result says: the worker it launched, or that it launched none. */
function launchResult(output: string | undefined): {
  failed: boolean
  threadId?: string
} {
  let result: unknown
  try {
    result = JSON.parse(output ?? "null")
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    return { failed: false }
  }
  if (result === null || typeof result !== "object") return { failed: false }
  if ("success" in result && result.success === false) return { failed: true }
  return {
    failed: false,
    threadId:
      "worker_thread_id" in result &&
      typeof result.worker_thread_id === "string" &&
      THREAD_ID.test(result.worker_thread_id)
        ? result.worker_thread_id
        : undefined,
  }
}

export function WorkerLaunchMessage({ chunk }: { chunk: ToolExecutionChunk }) {
  const pending = chunk.status === "pending" || chunk.status === "in_progress"
  const { failed, threadId } =
    chunk.status === "completed"
      ? launchResult(chunk.output)
      : { failed: chunk.status === "error", threadId: undefined }
  const status = pending
    ? "Starting…"
    : failed
      ? "Launch failed"
      : threadId
        ? "Started"
        : "Launch status unknown"
  return (
    <TaskMessageChip
      label={threadId ? `Worker ${shortThreadId(threadId)}` : "Worker"}
      status={status}
      threadId={threadId}
      content={
        typeof chunk.input?.description === "string"
          ? chunk.input.description
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
          threadId && (
            <IconButton
              asChild
              icon={ArrowSquareOutIcon}
              label={`Open ${label} thread`}
              size="xs"
              color="secondary"
              variant="plain"
            >
              <Link to="/agents/$threadId" params={{ threadId }} />
            </IconButton>
          )
        }
      >
        <div className="mt-space-1 max-w-full rounded-xl border border-default bg-surface-level-2 p-space-3">
          <div className="text-xs leading-relaxed break-words whitespace-pre-wrap text-primary">
            {content}
          </div>
          {timestamp && (
            <MessageTimestamp timestamp={timestamp} className="mt-space-2" />
          )}
        </div>
      </ExpandableMessageChip>
    </div>
  )
}
