import { memo, useEffect, useState } from "react"
import {
  useMessages,
  useStreamContext as useAgentThreadStream,
} from "@langchain/react"
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Loader2,
  PauseCircle,
  TriangleAlert,
} from "lucide-react"

import { SubagentActivity } from "./SubagentActivity"
import type {
  SubagentDisplayStatus,
  SubagentStep,
} from "@/features/agents/lib/subagentModel"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import {
  formatElapsed,
  subagentDescription,
  subagentDisplayStatus,
  subagentElapsedMs,
  subagentLabel,
  subagentMessagesDone,
  subagentOutcome,
  subagentResultFromMessages,
  subagentSteps,
} from "@/features/agents/lib/subagentModel"
import { useIsAgentRunActive } from "@/features/agents/lib/provider/useIsAgentRunActive"
import { useIsInAgentThreadStream } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useTickingNow } from "@/features/agents/lib/useTickingNow"

/** Indentation per nesting level for subagents that spawned subagents. */
const DEPTH_INDENT_PX = 10

/**
 * A single subagent spawned via the `task` tool. Outside a live thread there is
 * no run to consult, so the card renders from the chunk alone; inside one it
 * also reads the subagent's own message stream, which knows it has finished
 * before the parent's snapshot does.
 */
export const SubagentCard = memo(function SubagentCard({
  chunk,
  onStatus,
}: {
  chunk: ToolExecutionChunk
  onStatus?: (toolCallId: string, status: SubagentDisplayStatus) => void
}) {
  const namespace = chunk.subagent?.namespace
  return useIsInAgentThreadStream() && namespace && namespace.length > 0 ? (
    <LiveSubagentCard chunk={chunk} namespace={namespace} onStatus={onStatus} />
  ) : (
    <SubagentCardView
      chunk={chunk}
      runActive={false}
      steps={[]}
      onStatus={onStatus}
    />
  )
})

function LiveSubagentCard({
  chunk,
  namespace,
  onStatus,
}: {
  chunk: ToolExecutionChunk
  namespace: Array<string>
  onStatus?: (toolCallId: string, status: SubagentDisplayStatus) => void
}) {
  const stream = useAgentThreadStream()
  const messages = useMessages(stream, { namespace })
  const runActive = useIsAgentRunActive()

  return (
    <SubagentCardView
      chunk={chunk}
      runActive={runActive}
      steps={subagentSteps(messages)}
      // The parent's snapshot only settles once every sibling has returned, so
      // trust the subagent's own messages for both facts it gets wrong.
      done={subagentMessagesDone(messages)}
      result={subagentResultFromMessages(messages)}
      onStatus={onStatus}
    />
  )
}

function SubagentCardView({
  chunk,
  runActive,
  steps,
  done = false,
  result,
  onStatus,
}: {
  chunk: ToolExecutionChunk
  runActive: boolean
  steps: Array<SubagentStep>
  done?: boolean
  result?: string
  onStatus?: (toolCallId: string, status: SubagentDisplayStatus) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const snapshotStatus = subagentDisplayStatus(chunk, runActive)
  const status =
    done && snapshotStatus !== "error" ? "completed" : snapshotStatus
  const isRunning = status === "running"
  // Freezing the tick freezes the timer: it stops at the moment this subagent
  // finished, not when the whole fan-out did.
  const now = useTickingNow(isRunning)
  const elapsedMs = subagentElapsedMs(chunk.subagent, now)
  const description = subagentDescription(chunk)
  const outcome = result
    ? { kind: "result" as const, text: result }
    : subagentOutcome(chunk)
  // Depth 1 is a root-spawned subagent and sits flush with the group.
  const indent = Math.max(0, (chunk.subagent?.depth ?? 1) - 1) * DEPTH_INDENT_PX

  useEffect(() => {
    onStatus?.(chunk.toolCallId, status)
  }, [onStatus, chunk.toolCallId, status])

  return (
    <div
      className="flex min-w-0 flex-col gap-1.5 overflow-hidden rounded-lg border border-border bg-accent p-2.5"
      style={indent ? { marginLeft: indent } : undefined}
      data-testid="subagent-card"
      data-status={status}
      data-subagent={subagentLabel(chunk)}
    >
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex min-w-0 items-center gap-1.5 text-left"
        aria-expanded={expanded}
      >
        <StatusIcon status={status} />
        {/* The task, not the subagent type — every card in a fan-out shares a
            type, so it cannot tell them apart. */}
        <span
          className="truncate text-[11px] font-medium text-foreground"
          data-testid="subagent-title"
        >
          {description || subagentLabel(chunk)}
        </span>
        {elapsedMs !== undefined && (
          <span
            className="ml-auto shrink-0 text-[10px] text-muted-foreground/70 tabular-nums"
            data-testid="subagent-elapsed"
          >
            {formatElapsed(elapsedMs)}
          </span>
        )}
        {expanded ? (
          <ChevronDown
            className="h-3 w-3 shrink-0 text-muted-foreground/50"
            aria-hidden
          />
        ) : (
          <ChevronRight
            className="h-3 w-3 shrink-0 text-muted-foreground/50"
            aria-hidden
          />
        )}
      </button>

      <span className="truncate text-[10px] text-muted-foreground/60">
        {subagentLabel(chunk)}
      </span>

      {expanded && description && (
        <p className="text-[11px] leading-4 break-words whitespace-pre-wrap text-muted-foreground/70">
          {description}
        </p>
      )}

      <SubagentActivity steps={steps} expanded={expanded} />

      {status === "stalled" && (
        <p className="text-[10px] text-muted-foreground/50">
          Stopped without finishing.
        </p>
      )}

      {outcome && (
        <p
          className={`border-t border-border pt-1.5 text-[11px] leading-4 break-words whitespace-pre-wrap ${
            outcome.kind === "error"
              ? "text-red-400"
              : "text-muted-foreground/70"
          } ${expanded ? "" : "line-clamp-3"}`}
          data-testid={`subagent-${outcome.kind}`}
        >
          {outcome.text}
        </p>
      )}
    </div>
  )
}

function StatusIcon({
  status,
}: {
  status: ReturnType<typeof subagentDisplayStatus>
}) {
  if (status === "running")
    return (
      <Loader2
        className="h-3 w-3 shrink-0 animate-spin text-primary"
        aria-hidden
      />
    )
  if (status === "error")
    return (
      <TriangleAlert className="h-3 w-3 shrink-0 text-red-400" aria-hidden />
    )
  if (status === "stalled")
    return (
      <PauseCircle
        className="h-3 w-3 shrink-0 text-muted-foreground/50"
        aria-hidden
      />
    )
  return <Bot className="h-3 w-3 shrink-0 text-primary" aria-hidden />
}
