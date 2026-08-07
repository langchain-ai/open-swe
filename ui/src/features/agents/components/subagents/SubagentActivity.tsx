import { Check, Loader2, X } from "lucide-react"

import type { SubagentStep } from "@/features/agents/lib/subagentModel"

/** Nested steps kept visible when the card is collapsed. */
const COLLAPSED_STEPS = 1

/**
 * A subagent's nested tool calls. Steps come from the subagent's own messages
 * (see `subagentSteps`) because the server emits no `tools` events for a
 * subagent namespace, leaving the SDK's scoped tool projection empty.
 */
export function SubagentActivity({
  steps,
  expanded,
}: {
  steps: Array<SubagentStep>
  expanded: boolean
}) {
  if (steps.length === 0) return null

  const visible = expanded ? steps : steps.slice(-COLLAPSED_STEPS)
  const hidden = steps.length - visible.length

  return (
    <div
      className="mt-1 flex min-w-0 flex-col gap-1 border-t border-border pt-1.5"
      data-testid="subagent-activity"
      data-step-count={steps.length}
    >
      {hidden > 0 && (
        <span className="text-[10px] text-muted-foreground/50">
          +{hidden} earlier {hidden === 1 ? "step" : "steps"}
        </span>
      )}
      {visible.map((step) => (
        <div
          key={step.id}
          className="flex min-w-0 items-center gap-1.5"
          data-testid="subagent-activity-step"
        >
          <StepIcon status={step.status} />
          <span className="truncate font-mono text-[10px] text-muted-foreground/70">
            {step.label}
          </span>
        </div>
      ))}
    </div>
  )
}

function StepIcon({ status }: { status: SubagentStep["status"] }) {
  if (status === "completed") return <Check className="h-3 w-3 shrink-0 text-primary" aria-hidden />
  if (status === "error") return <X className="h-3 w-3 shrink-0 text-red-400" aria-hidden />
  return <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground/70" aria-hidden />
}
