import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ChevronDown, ChevronRight } from "@/components/glyphs"
import { cn } from "@/lib/utils"

const CHEVRON_MOTION_CLASS =
  "transition-transform duration-fast ease-out-quint motion-reduce:transition-none"

/**
 * Collapses a settled turn's work log behind a single "Worked for …" line, so
 * the transcript reads as replies until the reader asks for the details.
 */
export function TurnFoldRow({
  label,
  active,
  expanded,
  onToggle,
}: {
  label: string
  active: boolean
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="inline-flex min-h-5 cursor-pointer items-center gap-1.5 self-start rounded-compact text-label text-ink-subtle tabular-nums outline-none select-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary"
    >
      <Icon
        icon={ChevronRight}
        size="sm"
        className={cn(CHEVRON_MOTION_CLASS, expanded && "rotate-90")}
      />
      <span className={active ? "shimmer-text" : undefined}>{label}</span>
    </button>
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
      className="-mx-1.5 flex w-fit cursor-pointer items-center gap-2 rounded-compact px-1.5 py-0.5 text-left text-label text-ink-subtle transition-colors duration-fast ease-out-quint outline-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset motion-reduce:transition-none"
    >
      <span className="flex size-5 shrink-0 items-center justify-center">
        <Icon
          icon={ChevronDown}
          size="sm"
          className={cn(CHEVRON_MOTION_CLASS, expanded && "rotate-180")}
        />
      </span>
      <span className="font-medium">
        {expanded ? `Show fewer ${noun}` : `+${hiddenCount} previous ${noun}`}
      </span>
    </button>
  )
}
