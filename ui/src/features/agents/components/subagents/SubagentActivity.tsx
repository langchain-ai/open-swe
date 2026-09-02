import { useToolCalls } from "@langchain/react"
import { Check, Loader2, X } from "lucide-react"

import { humanizeToolName } from "@/features/agents/lib/toolNames"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

/**
 * Live status for a single subagent, read straight from the SDK's scoped
 * `tools` projection (`useToolCalls(stream, { namespace })`). The namespace
 * comes from `stream.subagents` (attached to the `task` chunk by
 * `streamMessagesToUi`), so this subscribes to exactly the subagent that the
 * parent card represents.
 *
 * Hotfix: rather than listing every nested tool call (which balloons the card),
 * this shows a single line with the subagent's current activity plus a running
 * step count. A richer activity UI will replace this later.
 *
 * Mounting opens a ref-counted subscription scoped to `namespace`; unmounting
 * closes it. Only mounted from {@link SubagentCard} when
 * `useIsInAgentThreadStream()` is true, so the runtime read is always inside
 * the agents layout provider.
 */
export function SubagentActivity({ namespace }: { namespace: Array<string> }) {
  const stream = useAgentThreadRuntime()
  const toolCalls = useToolCalls(stream, { namespace })

  const current = toolCalls[toolCalls.length - 1]
  if (!current) return null

  const stepCount = toolCalls.length

  return (
    <div className="mt-1 flex min-w-0 items-center gap-1.5 border-t border-border pt-1.5">
      {current.status === "finished" ? (
        <Check className="h-3 w-3 shrink-0 text-primary" aria-hidden />
      ) : current.status === "error" ? (
        <X className="h-3 w-3 shrink-0 text-red-400" aria-hidden />
      ) : (
        <Loader2
          className="h-3 w-3 shrink-0 animate-spin text-muted-foreground/70"
          aria-hidden
        />
      )}
      <span className="truncate text-[10px] text-muted-foreground/70">
        {humanizeToolName(current.name)}
      </span>
      <span className="ml-auto shrink-0 text-[10px] text-muted-foreground/70 tabular-nums">
        {stepCount} {stepCount === 1 ? "step" : "steps"}
      </span>
    </div>
  )
}
