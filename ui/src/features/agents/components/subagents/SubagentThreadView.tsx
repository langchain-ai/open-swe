import { ArrowLeftIcon } from "@langchain/macaw-components/icons"
import { useMemo, useState } from "react"
import { Link } from "@tanstack/react-router"
import { Badge } from "@langchain/macaw-components/Badge"
import { ChatState } from "@langchain/macaw-components/ChatState"
import { HumanMessage } from "@langchain/core/messages"
import { useMessages, useToolCalls } from "@langchain/react"
import type { SubagentDiscoverySnapshot } from "@langchain/react"

import { LoadError, useLoadTimedOut } from "@/components/LoadError"
import { ConversationLoading } from "@/features/agents/components/ConversationLoading"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { Messages } from "@/features/agents/components/messages"
import type { MessagesProps } from "@/features/agents/components/messages"
import { asString } from "@/features/agents/components/subagents/SubagentCard"
import { MessageClock } from "@/features/agents/lib/messageTimestamps"
import { useSubagentTimes } from "@/features/agents/lib/queries"
import type { AgentStream } from "@/features/agents/lib/stream/connection"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type {
  AcpToolStatus,
  AgentThread,
  Message,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"
import { cn } from "@/lib/utils"

/**
 * A subagent viewed as a thread of its own: the task it was given, then
 * everything it said and did under its namespace in the parent thread.
 * Subagents cannot be replied to (they run to completion on their own and
 * report back to the parent), so there is no composer.
 */
export function SubagentThreadView({
  thread,
  subagentId,
}: {
  thread: AgentThread
  subagentId: string
}) {
  const source = useThreadSource()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const sidebarCollapsed = useSidebarCollapsed()
  const transcriptTask =
    source.kind === "transcript" ? source.subagentTask(subagentId) : null
  // The stream finds only the root conversation's `task` calls, which start
  // every subagent the sidebar lists.
  const task =
    source.kind === "stream"
      ? source.messages
          .flatMap((message) => message.chunks)
          .find(
            (chunk): chunk is ToolExecutionChunk =>
              chunk.kind === "tool-execution" &&
              chunk.toolKind === "task" &&
              chunk.toolCallId === subagentId
          )
      : transcriptTask
  const description = asString(task?.input?.description)
  const transcriptMessages = useMemo(() => {
    if (!transcriptTask || source.kind !== "transcript") return []
    const own = source.subagentMessages([
      ...transcriptTask.namespace,
      transcriptTask.toolCallId,
    ])
    if (!description) return own
    // The transcript never records the task prompt as a human message — the
    // model wrote it, not a person — so the `task` call's input stands in.
    const prompt: Message = {
      id: `${transcriptTask.toolCallId}:prompt`,
      author: "user",
      timestamp: transcriptTask.startedAt,
      chunks: [{ kind: "text", text: description }],
    }
    return [prompt, ...own]
  }, [description, source, transcriptTask])

  const title = description.split("\n", 1)[0]?.trim() || "Subagent"
  const subagentType = asString(task?.input?.subagent_type)
  const isRunning = task?.status === "in_progress"
  const backLink = (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      search={{}}
      data-no-drag=""
      className="flex h-7 shrink-0 items-center gap-space-1 rounded-md px-space-1 text-secondary transition-colors duration-normal hover:bg-surface-level-1-hover hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
    >
      <ArrowLeftIcon size={14} weight="regular" aria-hidden />
      <span className="max-w-48 truncate text-xs" title={thread.title}>
        {thread.title}
      </span>
    </Link>
  )

  let body: React.ReactNode
  if (source.isHydrating) {
    body = <ConversationLoading label="Loading subagent" />
  } else if (!task && source.messages.length === 0 && source.error != null) {
    // `error` also holds a failed run's, so only a thread with no messages
    // failed to load.
    body = (
      <LoadError
        title="Unable to load subagent"
        context={`Thread: ${thread.id}`}
        error={source.error}
      />
    )
  } else if (!task) {
    body = (
      <LoadError
        title="Subagent not found"
        context={`Thread: ${thread.id}`}
        error={
          source.kind === "stream"
            ? "This thread started no subagent with that id."
            : "This thread's transcript has no subagent with that id. It may belong to a turn older than the loaded window."
        }
      />
    )
  } else {
    const view = {
      threadId: thread.id,
      scrollKey: `${thread.id}:${subagentId}`,
      isStreaming: isRunning,
      contentWidthClass: "max-w-3xl",
      footer: !isRunning && (
        <p className="px-space-1 pt-space-2 pb-space-6 text-center text-xs text-tertiary">
          Subagents cannot be replied to. Follow up in the parent thread.
        </p>
      ),
    }
    body =
      source.kind === "stream" ? (
        <StreamSubagentMessages
          stream={source.stream}
          toolCallId={subagentId}
          status={task.status}
          {...view}
        />
      ) : (
        <Messages messages={transcriptMessages} {...view} />
      )
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <header
        data-desktop-drag-region=""
        className="relative z-pane-header h-11 shrink-0 border-b border-subtle bg-surface-level-1/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-surface-level-1/60 after:to-transparent"
      >
        <div
          className={cn(
            "flex h-full w-full items-center gap-space-2 px-space-4",
            sidebarCollapsed && (isDesktop ? "pl-48" : "pl-space-8")
          )}
        >
          {backLink}
          <span className="text-quaternary" aria-hidden>
            /
          </span>
          <div className="flex min-w-0 items-center gap-space-2 text-sm font-medium text-primary">
            <span className="min-w-0 truncate" title={description || title}>
              {title}
            </span>
            {subagentType && (
              <Badge color="secondary" size="xs" className="shrink-0">
                {subagentType}
              </Badge>
            )}
            {task && (
              <ChatState
                announce={false}
                key={task.toolCallId}
                variant="simple"
                status={
                  isRunning
                    ? "streaming"
                    : task.status === "error"
                      ? "failed"
                      : "completed"
                }
                label={
                  isRunning
                    ? "Subagent running"
                    : task.status === "error"
                      ? "Subagent failed"
                      : "Subagent finished"
                }
                showElapsed={false}
              />
            )}
          </div>
        </div>
      </header>
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        {body}
      </div>
    </div>
  )
}

/**
 * A subagent's messages and tool calls, read through the SDK stream while this
 * is mounted: its saved state, and its events while it runs.
 */
function StreamSubagentMessages({
  stream,
  toolCallId,
  status,
  ...view
}: Omit<MessagesProps, "messages"> & {
  threadId: string
  stream: AgentStream
  toolCallId: string
  status: AcpToolStatus
}) {
  // The SDK's record of the subagent carries the namespace it streams under.
  // Without one, as for an earlier run's subagent on a page that joined a live
  // run as it opened, a target with the call id has the SDK look the namespace
  // up: in the `task` result, else in the thread's history.
  const target: Pick<SubagentDiscoverySnapshot, "id" | "namespace" | "status"> =
    stream.subagents.get(toolCallId) ?? {
      id: toolCallId,
      namespace: [`tools:${toolCallId}`],
      status:
        status === "completed"
          ? "complete"
          : status === "error"
            ? "error"
            : "running",
    }
  const messages = useMessages(stream, target)
  const toolCalls = useToolCalls(stream, target)
  const [clock] = useState(() => new MessageClock())
  const running = target.status === "running"
  // The subagent's messages carry no time, so the transcript's stand in; its
  // task prompt was sent as its `task` call started.
  const { data: times } = useSubagentTimes(view.threadId, toolCallId, running)
  const recorded = new Map(Object.entries(times?.messages ?? {}))
  const promptId = messages.find((message) =>
    HumanMessage.isInstance(message)
  )?.id
  if (promptId && times?.prompt) recorded.set(promptId, times.prompt)
  // A subagent starts from its task prompt, so an empty list is one not read yet.
  const timedOut = useLoadTimedOut(messages.length === 0)

  if (timedOut)
    return (
      <LoadError
        title="Subagent unavailable"
        context={`Thread: ${view.threadId}`}
        error="This subagent's messages could not be read."
      />
    )
  if (messages.length === 0)
    return <ConversationLoading label="Loading subagent" />
  return (
    <Messages
      messages={streamMessagesToUi(
        messages,
        toolCalls,
        clock.reading(running, recorded)
      )}
      {...view}
    />
  )
}
