import { memo } from "react"
import { Link as RouterLink } from "@tanstack/react-router"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { Link } from "@langchain/macaw-components/Link"
import { Spinner } from "@langchain/macaw-components/Spinner"

import { SubagentActivity } from "./SubagentActivity"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useOptionalThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { cn } from "@/lib/utils"

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
    <div className="flex min-w-0 flex-col gap-space-2 overflow-hidden rounded-lg border border-default bg-surface-level-2 p-space-2">
      <div className="flex min-w-0 items-center gap-space-2">
        {isRunning ? (
          <Spinner size="xxs" className="shrink-0 text-icon-brand" />
        ) : (
          <RobotIcon
            size={12}
            weight="regular"
            className={cn(
              "shrink-0",
              isError ? "text-icon-error" : "text-icon-brand"
            )}
            aria-hidden
          />
        )}
        <span className="truncate text-xxs font-medium text-secondary">
          {subagentType}
        </span>
        {source?.kind === "transcript" && namespace && namespace.length > 0 && (
          <Link
            as={
              <RouterLink
                to="/agents/$threadId"
                params={{ threadId: source.threadId }}
                search={{ subagent: chunk.toolCallId }}
              />
            }
            variant="xs"
            rightDecorator={ArrowUpRightIcon}
            className="ml-auto shrink-0"
            aria-label="Open subagent transcript"
            title="Open subagent transcript"
          >
            Open
          </Link>
        )}
      </div>
      {description && (
        <p className="line-clamp-5 text-xxs leading-4 break-words whitespace-pre-wrap text-tertiary">
          {description}
        </p>
      )}
      {activity}
    </div>
  )
})
