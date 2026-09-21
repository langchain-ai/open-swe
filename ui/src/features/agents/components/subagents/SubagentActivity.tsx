import { useToolCalls } from "@langchain/react"
import { Check, Loader2, X } from "lucide-react"

import { humanizeToolName } from "@/features/agents/lib/toolNames"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type { AgentStream } from "@/features/agents/lib/stream/connection"

type ActivityStatus = "in_progress" | "completed" | "error"

/**
 * Live status for a single subagent: its current activity plus a running step
 * count. The namespace comes from the parent `task` chunk, so this shows
 * exactly the subagent that card represents.
 *
 * Hotfix: rather than listing every nested tool call (which balloons the card),
 * this shows a single line. A richer activity UI will replace this later.
 */
export function SubagentActivity({ namespace }: { namespace: Array<string> }) {
  const source = useThreadSource()
  return <StreamActivity stream={source.stream} namespace={namespace} />
}

/**
 * Mounting opens a ref-counted subscription scoped to `namespace` on the SDK's
 * `tools` projection; unmounting closes it.
 */
function StreamActivity({
  stream,
  namespace,
}: {
  stream: AgentStream
  namespace: Array<string>
}) {
  const toolCalls = useToolCalls(stream, { namespace })
  const current = toolCalls[toolCalls.length - 1]
  if (!current) return null
  return (
    <ActivityLine
      name={current.name}
      status={
        current.status === "finished"
          ? "completed"
          : current.status === "error"
            ? "error"
            : "in_progress"
      }
      steps={toolCalls.length}
    />
  )
}

function ActivityLine({
  name,
  status,
  steps,
}: {
  name: string
  status: ActivityStatus
  steps: number
}) {
  return (
    <div className="mt-1 flex min-w-0 items-center gap-1.5 border-t border-border pt-1.5">
      {status === "completed" ? (
        <Check className="h-3 w-3 shrink-0 text-primary" aria-hidden />
      ) : status === "error" ? (
        <X className="h-3 w-3 shrink-0 text-red-400" aria-hidden />
      ) : (
        <Loader2
          className="h-3 w-3 shrink-0 animate-spin text-muted-foreground/70"
          aria-hidden
        />
      )}
      <span className="truncate text-[10px] text-muted-foreground/70">
        {humanizeToolName(name)}
      </span>
      <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/70 tabular-nums">
        {steps} {steps === 1 ? "step" : "steps"}
      </span>
    </div>
  )
}
