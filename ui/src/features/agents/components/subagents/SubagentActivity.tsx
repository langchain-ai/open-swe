import {
  useStreamContext as useAgentThreadStream,
  useToolCalls,
} from "@langchain/react"
import { Check, Loader2, X } from "lucide-react"
import type { ToolCallStatus } from "@langchain/react"

import {
  Task,
  TaskContent,
  TaskItem,
  TaskTrigger,
} from "@/components/ai-elements/task"
import { humanizeToolName } from "@/features/agents/lib/toolNames"

function StatusIcon({ status }: { status: ToolCallStatus }) {
  if (status === "finished") {
    return <Check className="size-3 shrink-0 text-primary" aria-hidden />
  }
  if (status === "error") {
    return <X className="size-3 shrink-0 text-destructive" aria-hidden />
  }
  return (
    <Loader2
      className="size-3 shrink-0 animate-spin text-muted-foreground/70"
      aria-hidden
    />
  )
}

/**
 * The nested tool calls of one subagent, read from the SDK's scoped `tools`
 * projection (`useToolCalls(stream, { namespace })`). Collapsed, it shows the
 * subagent's current step and a step count; expanded, every step so far.
 *
 * Mounting opens a ref-counted subscription scoped to `namespace`; unmounting
 * closes it. Only mounted from {@link SubagentCard} inside a `StreamProvider`.
 */
export function SubagentActivity({
  namespace,
}: {
  namespace: ReadonlyArray<string>
}) {
  const stream = useAgentThreadStream()
  const toolCalls = useToolCalls(stream, { namespace })

  const current = toolCalls[toolCalls.length - 1]
  if (!current) return null

  const stepCount = toolCalls.length
  const summary = `${humanizeToolName(current.name)} · ${stepCount} ${stepCount === 1 ? "step" : "steps"}`

  return (
    <Task className="mt-1 border-t border-border pt-1.5" defaultOpen={false}>
      <TaskTrigger title={summary}>
        <span className="flex min-w-0 items-center gap-1.5 text-[10px] text-muted-foreground/70">
          <StatusIcon status={current.status} />
          <span className="truncate">{humanizeToolName(current.name)}</span>
          <span className="ml-auto shrink-0 tabular-nums">
            {stepCount} {stepCount === 1 ? "step" : "steps"}
          </span>
        </span>
      </TaskTrigger>
      <TaskContent>
        <div className="mt-1 flex flex-col gap-0.5">
          {toolCalls.map((call) => (
            <TaskItem
              key={call.id}
              className="flex items-center gap-1.5 text-[10px] text-muted-foreground/70"
              data-testid="subagent-step"
            >
              <StatusIcon status={call.status} />
              <span className="truncate">{humanizeToolName(call.name)}</span>
            </TaskItem>
          ))}
        </div>
      </TaskContent>
    </Task>
  )
}
