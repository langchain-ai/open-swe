import { Markdown } from "@/features/agents/components/chat/Markdown"
import { AgentMark } from "@/features/reviews/page/AgentMark"

/** The scout's note on a tricky line, drawn as a margin note in the agent's colors. */
export function StepNote({ note }: { note: string }) {
  return (
    <div
      data-testid="step-note"
      className="w-full rounded-r-lg border-y border-r border-default bg-brand-muted text-xs"
      style={{ borderLeft: "3px solid var(--icon-brand)" }}
    >
      <div className="flex items-center gap-space-2 px-space-3 pt-space-2">
        <AgentMark />
        <span className="shrink-0 font-medium text-primary">Worth a look</span>
      </div>
      <div className="px-space-3 pt-space-1 pb-space-2 leading-[1.6]">
        <Markdown content={note} />
      </div>
    </div>
  )
}
