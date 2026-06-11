import { useStreamContext as useAgentThreadStream, useToolCalls } from "@langchain/react"
import { Check, Loader2, X } from "lucide-react"

function humanizeToolName(name: string): string {
  return name.replace(/_/g, " ").trim() || "tool"
}

/**
 * Live nested activity for a single subagent, read straight from the SDK's
 * scoped `tools` projection (`useToolCalls(stream, { namespace })`). The
 * namespace comes from `stream.subagents` (attached to the `task` chunk by
 * `streamMessagesToUi`), so this subscribes to exactly the subagent that the
 * parent card represents.
 *
 * Mounting opens a ref-counted subscription scoped to `namespace`; unmounting
 * closes it. Only mounted from {@link SubagentCard} when
 * `useIsInAgentThreadStream()` is true, so the `useStreamContext` read is
 * always inside a `StreamProvider`.
 */
export function SubagentActivity({ namespace }: { namespace: Array<string> }) {
  const stream = useAgentThreadStream()
  const toolCalls = useToolCalls(stream, { namespace })

  if (toolCalls.length === 0) return null

  return (
    <div className="mt-1 flex flex-col gap-1 border-t border-[var(--ui-border)] pt-1.5">
      {toolCalls.map((toolCall, index) => {
        const id = toolCall.id || toolCall.callId || `sub-tool-${index}`
        return (
          <div key={id} className="flex min-w-0 items-center gap-1.5">
            {toolCall.status === "finished" ? (
              <Check className="h-3 w-3 shrink-0 text-[color:var(--ui-accent)]" aria-hidden />
            ) : toolCall.status === "error" ? (
              <X className="h-3 w-3 shrink-0 text-red-400" aria-hidden />
            ) : (
              <Loader2
                className="h-3 w-3 shrink-0 animate-spin text-[color:var(--ui-text-dim)]"
                aria-hidden
              />
            )}
            <span className="truncate text-[10px] text-[color:var(--ui-text-dim)]">
              {humanizeToolName(toolCall.name)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
