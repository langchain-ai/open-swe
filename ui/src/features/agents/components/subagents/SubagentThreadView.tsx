import { useMemo } from "react"
import { Link } from "@tanstack/react-router"
import {
  ArrowLeftIcon,
  CheckCircleIcon,
  CircleNotchIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { LoadError } from "@/components/LoadError"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { Messages } from "@/features/agents/components/messages"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type { TranscriptToolCallState } from "@/features/agents/lib/transcript/reducer"
import type { AgentThread, Message } from "@/features/agents/lib/types"
import { cn } from "@/lib/utils"

/** Coerce an unknown tool-argument value to a trimmed string, or `""`. */
function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

function firstLine(text: string): string {
  return text.split("\n", 1)[0]?.trim() ?? ""
}

/**
 * The task prompt as the human message that opened the subagent's run. The
 * transcript never records it as one — the model wrote it, not a person — so
 * the `task` call's input is where it lives.
 */
function promptMessage(task: TranscriptToolCallState): Message | null {
  const description = asString(task.input.description)
  if (!description) return null
  return {
    id: `${task.toolCallId}:prompt`,
    author: "user",
    timestamp: task.startedAt,
    chunks: [{ kind: "text", text: description }],
  }
}

/**
 * A subagent viewed as a thread of its own: the task it was given, then
 * everything it said and did, read from the parent thread's transcript under
 * the subagent's namespace. Subagents cannot be replied to — they run to
 * completion on their own and report back to the parent — so there is no
 * composer.
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
  const task =
    source.kind === "transcript" ? source.subagentTask(subagentId) : null
  const namespace = useMemo(
    () => (task ? [...task.namespace, task.toolCallId] : null),
    [task]
  )
  const messages = useMemo(() => {
    if (!task || !namespace || source.kind !== "transcript") return []
    const prompt = promptMessage(task)
    const own = source.subagentMessages(namespace)
    return prompt ? [prompt, ...own] : own
  }, [namespace, source, task])

  const description = task ? asString(task.input.description) : ""
  const title = firstLine(description) || "Subagent"
  const subagentType = task ? asString(task.input.subagent_type) : ""
  const isRunning = task?.status === "in_progress"
  const backLink = (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      search={{}}
      data-no-drag=""
      className="flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <ArrowLeftIcon className="size-3.5" aria-hidden />
      <span className="max-w-48 truncate text-xs" title={thread.title}>
        {thread.title}
      </span>
    </Link>
  )

  let body: React.ReactNode
  if (source.kind !== "transcript") {
    body = (
      <LoadError
        title="Subagent transcript unavailable"
        context={`Thread: ${thread.id}`}
        error="Only threads served by the transcript log keep their subagents' transcripts."
      />
    )
  } else if (source.isHydrating) {
    body = (
      <div className="flex flex-1 items-center justify-center px-6">
        <img
          src={`${import.meta.env.BASE_URL}logo-mark.png`}
          alt="Loading subagent"
          className="size-12 animate-pulse"
        />
      </div>
    )
  } else if (!task) {
    body = (
      <LoadError
        title="Subagent not found"
        context={`Thread: ${thread.id}`}
        error="This thread's transcript has no subagent with that id. It may belong to a turn older than the loaded window."
      />
    )
  } else {
    body = (
      <Messages
        messages={messages}
        threadId={thread.id}
        showUserNames={false}
        scrollKey={`${thread.id}:${subagentId}`}
        isStreaming={isRunning}
        streamIsLoading={isRunning}
        isThinking={isRunning}
        contentWidthClass="max-w-3xl"
        emptyState={
          <div className="flex min-h-60 items-center justify-center">
            <p className="text-xs text-muted-foreground/70">
              This subagent has not done anything yet.
            </p>
          </div>
        }
        footer={
          !isRunning && (
            <p className="px-1 pt-2 pb-6 text-center text-xs text-muted-foreground/70">
              Subagents cannot be replied to. Follow up in the parent thread.
            </p>
          )
        }
      />
    )
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <header
        data-desktop-drag-region=""
        className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent"
      >
        <div
          className={cn(
            "flex h-full w-full items-center gap-2 px-4",
            sidebarCollapsed && (isDesktop ? "pl-32" : "pl-14")
          )}
        >
          {backLink}
          <span className="text-muted-foreground/50" aria-hidden>
            /
          </span>
          <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
            <span className="min-w-0 truncate" title={description || title}>
              {title}
            </span>
            {subagentType && (
              <Badge variant="outline" className="shrink-0">
                {subagentType}
              </Badge>
            )}
            {task?.status === "in_progress" ? (
              <CircleNotchIcon
                className="size-3.5 shrink-0 animate-spin text-muted-foreground"
                aria-label="Subagent running"
              />
            ) : task?.status === "error" ? (
              <WarningCircleIcon
                className="size-3.5 shrink-0 text-destructive"
                aria-label="Subagent failed"
              />
            ) : task ? (
              <CheckCircleIcon
                className="size-3.5 shrink-0 text-muted-foreground/70"
                aria-label="Subagent finished"
              />
            ) : null}
          </div>
        </div>
      </header>
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        {body}
      </div>
    </div>
  )
}
