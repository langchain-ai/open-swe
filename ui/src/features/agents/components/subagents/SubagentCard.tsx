import { memo } from "react"
import { Link } from "@tanstack/react-router"
import { ArrowUpRight, Bot, Loader2 } from "lucide-react"

import { SubagentActivity } from "./SubagentActivity"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useOptionalThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

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
  const source = useOptionalThreadSource()
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
    <div className="flex min-w-0 flex-col gap-1.5 overflow-hidden rounded-compact border border-line bg-hover p-2.5">
      <div className="flex min-w-0 items-center gap-1.5">
        {isRunning ? (
          <Loader2
            className="h-3 w-3 shrink-0 animate-spin text-primary"
            aria-hidden
          />
        ) : (
          <Bot
            className={`h-3 w-3 shrink-0 ${isError ? "text-risk" : "text-primary"}`}
            aria-hidden
          />
        )}
        <span className="truncate text-meta font-medium text-ink-subtle">
          {subagentType}
        </span>
        {source?.kind === "transcript" && namespace && namespace.length > 0 && (
          <Link
            to="/agents/$threadId"
            params={{ threadId: source.threadId }}
            search={{ subagent: chunk.toolCallId }}
            className="ml-auto flex shrink-0 items-center gap-0.5 rounded-tick px-1 text-meta text-ink-subtle/70 hover:bg-canvas hover:text-ink"
            aria-label="Open subagent transcript"
            title="Open subagent transcript"
          >
            Open
            <ArrowUpRight className="h-3 w-3" aria-hidden />
          </Link>
        )}
      </div>
      {description && (
        <p className="line-clamp-5 text-meta leading-4 break-words whitespace-pre-wrap text-ink-subtle/70">
          {description}
        </p>
      )}
      {activity}
    </div>
  )
})
