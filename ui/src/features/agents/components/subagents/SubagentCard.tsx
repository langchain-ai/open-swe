import { memo } from "react"
import { Link } from "@tanstack/react-router"

import { SubagentActivity } from "./SubagentActivity"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import { ArrowUpRight, Bot } from "@/components/glyphs"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useOptionalThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

/** Coerce an unknown tool-argument value to a trimmed string, or `""`. */
export function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

/**
 * A single subagent spawned via the `task` tool: its type, the brief it was
 * given (the `description`), and while it runs, what it is doing now.
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
    <Stack
      gap="xs"
      bg="panel"
      border="line"
      radius="control"
      className="min-w-0 overflow-hidden px-3 py-2"
    >
      <Inline gap="sm" align="center" className="min-h-control-sm min-w-0">
        {isRunning ? (
          <Spinner size="sm" className="text-ink-subtle" />
        ) : (
          <Icon
            icon={Bot}
            size="sm"
            className={isError ? "text-risk" : "text-ink-subtle"}
          />
        )}
        <Box
          render={<span />}
          className="min-w-0 flex-1 truncate text-label font-medium text-ink"
        >
          {subagentType}
        </Box>
        {source?.kind === "transcript" && namespace && namespace.length > 0 && (
          <Button
            variant="ghost"
            size="compact"
            nativeButton={false}
            className="-mr-1.5 shrink-0 gap-1 px-1.5 text-ink-subtle hover:text-ink"
            aria-label="Open subagent transcript"
            title="Open subagent transcript"
            render={
              <Link
                to="/agents/$threadId"
                params={{ threadId: source.threadId }}
                search={{ subagent: chunk.toolCallId }}
              />
            }
          >
            Open
            <Icon icon={ArrowUpRight} size="sm" />
          </Button>
        )}
      </Inline>
      {description && (
        <Box
          render={<p />}
          className="line-clamp-5 text-meta break-words whitespace-pre-wrap text-ink-subtle"
        >
          {description}
        </Box>
      )}
      {activity}
    </Stack>
  )
})
