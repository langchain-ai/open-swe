import { useCallback, useState } from "react"

import { SubagentCard } from "./SubagentCard"
import type { SubagentDisplayStatus } from "@/features/agents/lib/subagentModel"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import {
  formatElapsed,
  subagentDisplayStatus,
  summarizeSubagentGroup,
} from "@/features/agents/lib/subagentModel"
import { useIsAgentRunActive } from "@/features/agents/lib/provider/useIsAgentRunActive"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useTickingNow } from "@/features/agents/lib/useTickingNow"

/** Maximum number of subagent cards rendered per row. */
const MAX_SUBAGENT_COLUMNS = 4

/**
 * Renders the subagents from a `subagent-group` render item as a responsive
 * card grid under a rollup header. The column count follows the number of
 * cards up to {@link MAX_SUBAGENT_COLUMNS}, so 1–4 subagents fill the row
 * evenly and 5+ wrap onto additional rows.
 */
export function SubagentGroup({
  chunks,
}: {
  chunks: Array<ToolExecutionChunk>
}) {
  return useIsInAgentThreadStream() ? (
    <LiveSubagentGroup chunks={chunks} />
  ) : (
    <SubagentGroupView chunks={chunks} runActive={false} />
  )
}

function LiveSubagentGroup({ chunks }: { chunks: Array<ToolExecutionChunk> }) {
  const runActive = useIsAgentRunActive()
  return <SubagentGroupView chunks={chunks} runActive={runActive} />
}

function SubagentGroupView({
  chunks,
  runActive,
}: {
  chunks: Array<ToolExecutionChunk>
  runActive: boolean
}) {
  const columns = Math.min(Math.max(chunks.length, 1), MAX_SUBAGENT_COLUMNS)
  const [reported, setReported] = useState<
    ReadonlyMap<string, SubagentDisplayStatus>
  >(new Map())
  const onStatus = useCallback(
    (toolCallId: string, status: SubagentDisplayStatus) => {
      setReported((prev) => {
        if (prev.get(toolCallId) === status) return prev
        const next = new Map(prev)
        next.set(toolCallId, status)
        return next
      })
    },
    []
  )

  const statusOf = (chunk: ToolExecutionChunk) =>
    reported.get(chunk.toolCallId) ?? subagentDisplayStatus(chunk, runActive)
  const anyRunning = chunks.some((chunk) => statusOf(chunk) === "running")
  const now = useTickingNow(anyRunning)
  const summary = summarizeSubagentGroup(chunks, now, runActive, reported)

  return (
    <div className="flex min-w-0 flex-col gap-1.5" data-testid="subagent-group">
      <div className="flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground/70">
        <span className="truncate" data-testid="subagent-group-headline">
          {summary.headline}
        </span>
        {summary.failed > 0 && (
          <span className="shrink-0 text-red-400">{summary.failed} failed</span>
        )}
        {summary.elapsedMs !== undefined && (
          <span className="ml-auto shrink-0 tabular-nums">
            {formatElapsed(summary.elapsedMs)}
          </span>
        )}
      </div>
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {chunks.map((chunk) => (
          <SubagentCard
            key={chunk.toolCallId}
            chunk={chunk}
            onStatus={onStatus}
          />
        ))}
      </div>
    </div>
  )
}
