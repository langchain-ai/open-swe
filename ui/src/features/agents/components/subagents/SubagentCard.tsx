import { memo, useState } from "react"
import { Link as RouterLink } from "@tanstack/react-router"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"
import { Button } from "@langchain/macaw-components/Button"
import {
  CaretDownIcon,
  CaretRightIcon,
} from "@langchain/macaw-components/icons"
import { Link } from "@langchain/macaw-components/Link"
import { ChatState } from "@langchain/macaw-components/ChatState"

import { SubagentActivity } from "./SubagentActivity"
import {
  ToolResultBody,
  useLazyOutput,
} from "@/features/agents/components/messages/timeline/ToolResultBody"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useOptionalThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

function SubagentResultDisclosure({ chunk }: { chunk: ToolExecutionChunk }) {
  const [expanded, setExpanded] = useState(false)
  const loadOutput = chunk.loadOutput
  const { loadedText, loadError } = useLazyOutput(loadOutput, expanded)

  const preview = chunk.output?.trim() || null
  const resultText = loadedText ?? preview
  if (!resultText && !loadOutput) return null

  const Caret = expanded ? CaretDownIcon : CaretRightIcon
  const isError = chunk.status === "error"
  const label = isError ? "Error details" : "Result"

  return (
    <div className="flex min-w-0 flex-col gap-space-1 border-t border-subtle pt-space-1">
      <Button
        type="button"
        variant="plain"
        color="secondary"
        size="sm"
        aria-label={label}
        aria-expanded={expanded}
        onClick={() => setExpanded((open) => !open)}
        className="h-auto w-fit cursor-pointer justify-start gap-space-1 border-0 px-0 py-0 text-xxs text-secondary hover:text-primary"
      >
        <Caret size={10} weight="bold" aria-hidden className="shrink-0" />
        <span className={isError ? "text-error-secondary" : undefined}>
          {label}
        </span>
      </Button>
      {expanded &&
        (resultText !== null ? (
          <ToolResultBody value={resultText} />
        ) : (
          <p className="text-xxs text-secondary">
            {loadError ?? "Loading output…"}
          </p>
        ))}
    </div>
  )
}

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
  const namespace = chunk.subagentNamespace
  const activity =
    inLiveStream && namespace && namespace.length > 0 ? (
      <SubagentActivity namespace={namespace} />
    ) : null

  return (
    <div className="flex min-w-0 flex-col gap-space-2 overflow-hidden rounded-lg border border-default bg-surface-level-2 p-space-2">
      <div className="flex min-w-0 items-center gap-space-2">
        <ChatState
          announce={false}
          key={chunk.toolCallId}
          variant="simple"
          status={
            chunk.status === "error"
              ? "failed"
              : chunk.status === "completed"
                ? "completed"
                : "streaming"
          }
          label={
            chunk.status === "error"
              ? `${subagentType} failed`
              : chunk.status === "completed"
                ? `${subagentType} finished`
                : `${subagentType} running`
          }
          showElapsed={false}
        />
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
      {(chunk.status === "completed" || chunk.status === "error") && (
        <SubagentResultDisclosure chunk={chunk} />
      )}
    </div>
  )
})
