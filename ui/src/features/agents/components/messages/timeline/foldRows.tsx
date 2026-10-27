import { Button } from "@langchain/macaw-components/Button"
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
        // The border is there while live too, so settling doesn't nudge what follows.
        "border-b pt-space-1 pb-space-2",
        active ? "border-transparent" : "border-subtle"
      )}
    >
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={label}
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="h-auto max-w-full cursor-pointer justify-start gap-space-1 border-0 px-space-1 text-left text-secondary tabular-nums select-none hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
      >
        {active && <LoadingIndicator className="mr-space-1 size-3 shrink-0" />}
        <span className={cn("min-w-0 truncate", active && "shimmer-text")}>
          {label}
        </span>
        {active && <ElapsedSeconds />}
        <Caret size={12} weight="bold" aria-hidden className="shrink-0" />
      </Button>
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
    <Button
      color="secondary"
      variant="plain"
      size="xs"
      aria-label={
        expanded ? `Show fewer ${noun}` : `+${hiddenCount} previous ${noun}`
      }
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="w-full cursor-pointer justify-start gap-space-2 px-0.5 py-0.5 text-left leading-5 hover:bg-surface-level-1-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
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
    </Button>
  )
}
