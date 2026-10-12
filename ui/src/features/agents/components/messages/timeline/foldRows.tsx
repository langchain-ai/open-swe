import {
  CaretDownIcon,
  CaretRightIcon,
} from "@langchain/macaw-components/icons"
import { LoadingIndicator } from "@langchain/macaw-components/ThinkingState"
import { cn } from "@/lib/utils"
import { ElapsedSeconds } from "@/features/agents/components/messages/ElapsedSeconds"

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
  const Caret = expanded ? CaretDownIcon : CaretRightIcon

  return (
    <div
      className={cn(
        "pt-space-1 pb-space-2",
        !active && "border-b border-subtle"
      )}
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex cursor-pointer items-center gap-space-1 rounded-md px-space-1 text-xs text-secondary tabular-nums transition-colors duration-normal select-none hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
      >
        {active && <LoadingIndicator className="mr-space-1 size-3" />}
        <span className={active ? "shimmer-text" : undefined}>{label}</span>
        {active && <ElapsedSeconds />}
        <Caret size={12} weight="bold" aria-hidden />
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
      className="flex w-full cursor-pointer items-center gap-space-2 rounded-md px-0.5 py-0.5 text-left text-xxs leading-5 transition-colors duration-normal hover:bg-surface-level-1-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
    >
      <span className="flex size-5 shrink-0 items-center justify-center text-icon-tertiary">
        <CaretDownIcon
          size={14}
          weight="regular"
          className={cn(
            "shrink-0 transition-transform duration-normal",
            expanded && "rotate-180"
          )}
          aria-hidden
        />
      </span>
      <span className="font-medium text-primary">
        {expanded ? `Show fewer ${noun}` : `+${hiddenCount} previous ${noun}`}
      </span>
    </button>
  )
}
