import { Button } from "@langchain/macaw-components/Button"
import {
  CaretDownIcon,
  CaretRightIcon,
} from "@langchain/macaw-components/icons"
import { ChatState } from "@langchain/macaw-components/ChatState"
import { cn } from "@/lib/utils"

/**
 * Collapses a settled turn's work log behind a single "Worked for …" line, so
 * the transcript reads as replies until the reader asks for the details.
 */
export function TurnFoldRow({
  label,
  detail,
  active,
  since,
  expanded,
  onToggle,
}: {
  label: string
  /** Quieter text after the label, such as what woke a stretch of runs. */
  detail?: string
  active: boolean
  since?: number
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
        aria-label={detail ? `${label} · ${detail}` : label}
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="h-auto max-w-full cursor-pointer justify-start gap-space-1 border-0 px-space-1 text-left text-secondary tabular-nums select-none hover:text-primary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
      >
        {active ? (
          <ChatState
            className="min-w-0"
            status="streaming"
            label={<span className="truncate">{label}</span>}
            showElapsed
            startedAt={since}
            announce={false}
          />
        ) : (
          <span className="min-w-0 truncate">{label}</span>
        )}
        {detail && (
          <span className="min-w-0 shrink-[2] truncate text-tertiary">
            · {detail}
          </span>
        )}
        <Caret size={12} weight="bold" aria-hidden className="shrink-0" />
      </Button>
    </div>
  )
}
