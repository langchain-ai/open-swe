import { ArrowLeftIcon } from "@langchain/macaw-components/icons"
import { useMemo } from "react"
import { Link } from "@tanstack/react-router"
import { CheckCircleIcon } from "@phosphor-icons/react/dist/ssr/CheckCircle"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { Badge } from "@langchain/macaw-components/Badge"
import { Spinner } from "@langchain/macaw-components/Spinner"

import { LoadError } from "@/components/LoadError"
import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { Messages } from "@/features/agents/components/messages"
import { asString } from "@/features/agents/components/subagents/SubagentCard"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type { AgentThread, Message } from "@/features/agents/lib/types"
import { cn } from "@/lib/utils"

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
  const description = task ? asString(task.input.description) : ""
  const messages = useMemo(() => {
    if (!task || source.kind !== "transcript") return []
    const own = source.subagentMessages([...task.namespace, task.toolCallId])
    if (!description) return own
    // The transcript never records the task prompt as a human message — the
    // model wrote it, not a person — so the `task` call's input stands in.
    const prompt: Message = {
      id: `${task.toolCallId}:prompt`,
      author: "user",
      timestamp: task.startedAt,
      chunks: [{ kind: "text", text: description }],
    }
    return [prompt, ...own]
  }, [description, source, task])

  const title = description.split("\n", 1)[0]?.trim() || "Subagent"
  const subagentType = task ? asString(task.input.subagent_type) : ""
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
      <div
        role="status"
        aria-label="Loading subagent"
        className="flex flex-1 items-center justify-center px-space-5"
      >
        <img
          src={`${import.meta.env.BASE_URL}logo-mark.png`}
          alt=""
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
        scrollKey={`${thread.id}:${subagentId}`}
        isStreaming={isRunning}
        contentWidthClass="max-w-3xl"
        footer={
          !isRunning && (
            <p className="px-space-1 pt-space-2 pb-space-6 text-center text-xs text-tertiary">
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
        className="relative z-pane-header h-11 shrink-0 border-b border-subtle bg-surface-level-1/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-surface-level-1/60 after:to-transparent"
      >
        <div
          className={cn(
            "flex h-full w-full items-center gap-space-2 px-space-4",
            sidebarCollapsed && (isDesktop ? "pl-32" : "pl-space-8")
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
            {task?.status === "in_progress" ? (
              <span role="img" aria-label="Subagent running" className="flex">
                <Spinner size="xs" className="shrink-0 text-icon-secondary" />
              </span>
            ) : task?.status === "error" ? (
              <WarningCircleIcon
                size={14}
                weight="regular"
                className="shrink-0 text-icon-error"
                role="img"
                aria-label="Subagent failed"
              />
            ) : task ? (
              <CheckCircleIcon
                size={14}
                weight="regular"
                className="shrink-0 text-icon-tertiary"
                role="img"
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
