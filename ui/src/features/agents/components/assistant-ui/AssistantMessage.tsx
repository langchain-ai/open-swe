import {
  ActionBarPrimitive,
  MessagePrimitive,
  useAuiState,
} from "@assistant-ui/react"
import { Check, ChevronRight, CircleAlert, Copy, Loader2 } from "lucide-react"

import { AssistantSubagents } from "./AssistantSubagents"
import { Markdown } from "../chat/Markdown"
import { OutputIframe } from "../chat/OutputIframe"
import { DiffView } from "../chat/DiffView"
import type { ApprovalCallbacks } from "../messages/types"
import type {
  Message,
  TodoItem,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"

function isRoutineActivity(chunk: ToolExecutionChunk) {
  return chunk.status !== "error" && !chunk.approvalRequestId
}

function Tool({
  chunk,
  ...callbacks
}: { chunk: ToolExecutionChunk } & ApprovalCallbacks) {
  if (chunk.toolKind === "task") return <AssistantSubagents chunks={[chunk]} />
  const running = chunk.status === "pending" || chunk.status === "in_progress"
  return (
    <details
      className="my-3 rounded-xl border border-border text-sm"
      open={chunk.approvalRequestId ? true : undefined}
    >
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2.5 text-muted-foreground">
        {running ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin" />
        ) : chunk.status === "error" ? (
          <CircleAlert className="size-3.5 shrink-0 text-destructive" />
        ) : (
          <Check className="size-3.5 shrink-0" />
        )}
        <span className="min-w-0 flex-1 truncate">{chunk.title}</span>
        <span
          className={chunk.status === "error" ? "text-destructive" : "text-xs"}
        >
          {running
            ? "Running"
            : chunk.status === "error"
              ? "Failed"
              : "Complete"}
        </span>
      </summary>
      <div className="space-y-3 border-t border-border p-3">
        {chunk.input && (
          <pre className="max-h-48 overflow-auto text-xs whitespace-pre-wrap">
            {JSON.stringify(chunk.input, null, 2)}
          </pre>
        )}
        {chunk.output && (
          <pre className="max-h-72 overflow-auto rounded-lg bg-muted p-3 text-xs whitespace-pre-wrap">
            {chunk.output}
          </pre>
        )}
        {chunk.display && <OutputIframe display={chunk.display} />}
        {chunk.diffData && <DiffView diffData={chunk.diffData} snippet />}
        {chunk.locations?.map((location) => (
          <button
            key={location.path}
            type="button"
            onClick={() => callbacks.onOpenFile?.(location.path)}
            className="block text-xs underline"
          >
            {location.path}
          </button>
        ))}
        {chunk.approvalRequestId && (
          <div className="flex gap-2">
            {callbacks.onApprove && (
              <button
                type="button"
                className="rounded bg-primary px-3 py-1 text-primary-foreground"
                onClick={() => callbacks.onApprove?.(chunk.approvalRequestId!)}
              >
                Approve
              </button>
            )}
            {callbacks.onReject && (
              <button
                type="button"
                onClick={() => callbacks.onReject?.(chunk.approvalRequestId!)}
              >
                Reject
              </button>
            )}
          </div>
        )}
      </div>
    </details>
  )
}

export function AssistantMessage(callbacks: ApprovalCallbacks) {
  const role = useAuiState((s) => s.message.role)
  const running = useAuiState((s) => s.message.status?.type === "running")
  const source = useAuiState(
    (s) => s.message.metadata.custom.source as Message | undefined
  )
  const isContext =
    role === "system" || source?.structuredSenderKind === "system"
  const activity = source?.chunks.filter(
    (chunk): chunk is ToolExecutionChunk =>
      role === "assistant" &&
      !isContext &&
      chunk.kind === "tool-execution" &&
      isRoutineActivity(chunk)
  ) ?? []
  const activityRunning = activity.some(
    (chunk) => chunk.status === "pending" || chunk.status === "in_progress"
  )
  const content = (
    <MessagePrimitive.Parts>
      {({ part }) => {
        if (part.type === "text")
          return role === "user" ? (
            <div className="break-words whitespace-pre-wrap">{part.text}</div>
          ) : (
            <Markdown content={part.text} isLive={running} />
          )
        if (part.type === "image")
          return (
            <img
              src={part.image}
              alt={part.filename ?? "Attached image"}
              className="my-2 max-h-72 max-w-full rounded-xl"
            />
          )
        if (part.type === "reasoning")
          return (
            <details className="my-3 text-sm text-muted-foreground">
              <summary className="cursor-pointer">
                {running ? "Thinking…" : "Reasoning"}
              </summary>
              <div className="mt-2 border-l-2 border-border pl-3 whitespace-pre-wrap">
                {part.text}
              </div>
            </details>
          )
        if (part.type === "tool-call" && part.artifact) {
          const chunk = part.artifact as ToolExecutionChunk
          if (role === "assistant" && !isContext && isRoutineActivity(chunk)) return null
          return <Tool chunk={chunk} {...callbacks} />
        }
        if (part.type === "data" && part.name === "error")
          return (
            <div
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-destructive"
            >
              {String(part.data)}
            </div>
          )
        if (part.type === "data" && part.name === "todos")
          return (
            <ul className="my-3 space-y-1 text-sm">
              {(part.data as TodoItem[]).map((todo, index) => (
                <li key={index} className="flex gap-2">
                  <span>
                    {todo.status === "completed"
                      ? "✓"
                      : todo.status === "in_progress"
                        ? "◉"
                        : "○"}
                  </span>
                  {todo.content}
                </li>
              ))}
            </ul>
          )
        return null
      }}
    </MessagePrimitive.Parts>
  )

  return (
    <MessagePrimitive.Root
      className={`group/message my-6 min-w-0 ${role === "user" && !isContext ? "ml-auto max-w-[85%]" : "w-full"}`}
      data-message-id={source?.id}
    >
      {source?.structuredSenderName && (
        <div className="mb-1.5 text-xs text-muted-foreground">
          {source.structuredSenderName}
          {source.structuredSurface === "slack" ? " · Slack" : ""}
        </div>
      )}
      {isContext ? (
        <details className="rounded-xl border border-border p-3 text-sm text-muted-foreground">
          <summary className="cursor-pointer">
            {source?.structuredSenderName ?? "Context"}
          </summary>
          {content}
        </details>
      ) : (
        <div
          className={
            role === "user"
              ? "rounded-2xl bg-muted px-4 py-3 text-sm"
              : "space-y-2 px-1 leading-7"
          }
        >
          {activity.length > 0 && (
            <details className="group/activity my-3 text-sm text-muted-foreground">
              <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md py-1 focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
                {activityRunning ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ChevronRight className="size-3.5 transition-transform group-open/activity:rotate-90" />
                )}
                <span className="group-open/activity:hidden">Show activity</span>
                <span className="hidden group-open/activity:inline">Hide activity</span>
                <span className="text-xs">
                  · {activity.length} {activity.length === 1 ? "call" : "calls"}
                  {activityRunning && " · Running"}
                </span>
              </summary>
              {activity.map((chunk) => (
                <Tool key={chunk.toolCallId} chunk={chunk} {...callbacks} />
              ))}
            </details>
          )}
          {content}
        </div>
      )}
      {role === "assistant" && !running && (
        <ActionBarPrimitive.Root className="mt-2 flex">
          <ActionBarPrimitive.Copy
            aria-label="Copy message"
            className="rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover/message:opacity-100 hover:bg-muted focus-visible:opacity-100"
          >
            <Copy className="size-3.5" />
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
      )}
    </MessagePrimitive.Root>
  )
}
