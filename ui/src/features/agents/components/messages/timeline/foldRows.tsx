import { ChevronDown, ChevronRight } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * Collapses a settled turn's work log behind a single "Worked for …" line, so
 * the transcript reads as replies until the reader asks for the details.
 */
export function TurnFoldRow({
  label,
  expanded,
  onToggle,
}: {
  label: string
  expanded: boolean
  onToggle: () => void
}) {
  const Icon = expanded ? ChevronDown : ChevronRight

  return (
    <div className="border-b border-border/60 pt-1 pb-2">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex cursor-pointer items-center gap-1 rounded-md px-1 text-[13px] text-muted-foreground tabular-nums transition-colors select-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:outline-none focus-visible:ring-inset"
      >
        <span>{label}</span>
        <Icon className="size-3.5" />
      </button>
    </div>
  )
}

/**
 * Reveals the earlier entries of a work group; only the most recent stay
 * visible while a group is collapsed.
 */
export function WorkGroupToggleRow({
  hiddenCount,
  expanded,
  onToggle,
}: {
  hiddenCount: number
  expanded: boolean
  onToggle: () => void
}) {
  const noun = hiddenCount === 1 ? "tool call" : "tool calls"

  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="flex w-full cursor-pointer items-center gap-1.5 rounded-md px-0.5 py-0.5 text-left text-[12px] leading-5 transition-colors duration-150 hover:bg-accent/20 focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:outline-none focus-visible:ring-inset"
    >
      <span className="flex size-5 shrink-0 items-center justify-center text-muted-foreground/65">
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 opacity-70 transition-transform duration-200",
            expanded && "rotate-180"
          )}
          aria-hidden
        />
      </span>
      <span className="font-medium text-foreground/82">
        {expanded ? `Show fewer ${noun}` : `+${hiddenCount} previous ${noun}`}
      </span>
    </button>
  )
}
