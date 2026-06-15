import { useStreamContext as useAgentThreadStream, useToolCalls } from "@langchain/react"
import { Check, Loader2, X } from "lucide-react"

function humanizeToolName(name: string): string {
  return name.replace(/_/g, " ").trim() || "tool"
}

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
 * `useIsInAgentThreadStream()` is true, so the `useStreamContext` read is
 * always inside a `StreamProvider`.
 */
export function SubagentActivity({ namespace }: { namespace: Array<string> }) {
  const stream = useAgentThreadStream()
  const toolCalls = useToolCalls(stream, { namespace })

  const current = toolCalls[toolCalls.length - 1]
  if (!current) return null

  const stepCount = toolCalls.length

  return (
    <div className="mt-1 flex min-w-0 items-center gap-1.5 border-t border-[var(--ui-border)] pt-1.5">
      {current.status === "finished" ? (
        <Check className="h-3 w-3 shrink-0 text-[color:var(--ui-accent)]" aria-hidden />
      ) : current.status === "error" ? (
        <X className="h-3 w-3 shrink-0 text-red-400" aria-hidden />
      ) : (
        <Loader2
          className="h-3 w-3 shrink-0 animate-spin text-[color:var(--ui-text-dim)]"
          aria-hidden
        />
      )}
      <span className="truncate text-[10px] text-[color:var(--ui-text-dim)]">
        {humanizeToolName(current.name)}
      </span>
      <span className="ml-auto shrink-0 text-[10px] tabular-nums text-[color:var(--ui-text-dim)]">
        {stepCount} {stepCount === 1 ? "step" : "steps"}
      </span>
    </div>
  )
}
