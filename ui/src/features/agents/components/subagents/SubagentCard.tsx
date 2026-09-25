import { memo } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowUpRight, Bot } from "lucide-react"

import { SubagentActivity } from "./SubagentActivity"
import { Spinner } from "@/components/ui/spinner"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

/** Coerce an unknown tool-argument value to a trimmed string, or `""`. */
export function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

/**
 * A single subagent spawned via the `task` tool. Shows the subagent type and
 * the task input (its `description`) as a compact rectangle.
 */
export const SubagentCard = memo(function SubagentCard({
  chunk,
}: {
  chunk: ToolExecutionChunk
}) {
  const inLiveStream = useIsInAgentThreadStream()
  const source = useThreadSource()
  const input = chunk.input ?? {}
  const subagentType = asString(input.subagent_type) || "subagent"
  const description = asString(input.description)
  const isRunning = chunk.status === "in_progress" || chunk.status === "pending"
  const isError = chunk.status === "error"
  const namespace = chunk.subagentNamespace
  const activity =
    inLiveStream && namespace && namespace.length > 0 ? (
      <SubagentActivity namespace={namespace} />
    ) : null

  return (
    <div className="flex min-w-0 flex-col gap-1.5 overflow-hidden rounded-lg border border-border bg-accent p-2.5">
      <div className="flex min-w-0 items-center gap-1.5">
        {isRunning ? (
          <Spinner className="size-3 text-primary" aria-hidden />
        ) : (
          <Bot
            className={`h-3 w-3 shrink-0 ${isError ? "text-destructive" : "text-primary"}`}
            aria-hidden
          />
        )}
        <span className="truncate text-[11px] font-medium text-muted-foreground">
          {subagentType}
        </span>
        {source.kind === "transcript" && namespace && namespace.length > 0 && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: source.threadId }}
            search={{ subagent: chunk.toolCallId }}
            className="ml-auto flex shrink-0 items-center gap-0.5 rounded px-1 text-[10px] text-muted-foreground/70 hover:bg-background hover:text-foreground"
            aria-label="Open subagent transcript"
            title="Open subagent transcript"
          >
            Open
            <ArrowUpRight className="h-3 w-3" aria-hidden />
          </Link>
        )}
      </div>
      {description && (
        <p className="line-clamp-5 text-[11px] leading-4 break-words whitespace-pre-wrap text-muted-foreground/70">
          {description}
        </p>
      )}
      {activity}
    </div>
  )
})
