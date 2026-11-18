import { ChatState } from "@langchain/macaw-components/ChatState"
import { useToolCalls } from "@langchain/react"

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
  if (source.kind === "stream") {
    return <StreamActivity stream={source.stream} namespace={namespace} />
  }
  const calls = source.subagentToolCalls(namespace)
  const current = calls[calls.length - 1]
  if (!current) return null
  return (
    <ActivityLine
      name={current.name}
      status={current.status}
      steps={calls.length}
    />
  )
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
    <div className="mt-space-1 flex min-w-0 items-center gap-space-2 border-t border-default pt-space-1">
      <ChatState
        announce={false}
        status={
          status === "completed"
            ? "completed"
            : status === "error"
              ? "failed"
              : "streaming"
        }
        label={`${humanizeToolName(name)} · ${status === "error" ? "failed" : status === "completed" ? "completed" : "running"}`}
        className="min-w-0"
      />
      <span className="ml-auto shrink-0 text-xxs text-tertiary tabular-nums">
        {steps} {steps === 1 ? "step" : "steps"}
      </span>
    </div>
  )
}
