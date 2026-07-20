import { cn } from "@/lib/utils"
import { formatTokenCount } from "@/features/agents/lib/contextUsage"

export interface ContextWindowIndicatorProps {
  usedTokens?: number | null
  contextWindow?: number | null
  hasMessages?: boolean
  compact?: boolean
}

const RING_SIZE = 14
const RING_STROKE = 1.5
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS
const NEAR_LIMIT = 0.75
const AT_LIMIT = 0.9

function cleanTokenCount(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null
}

function contextLabel(
  usedTokens: number | null,
  contextWindow: number | null,
  hasMessages: boolean
): string {
  if (usedTokens != null && contextWindow != null) {
    const percent = Math.round((usedTokens / contextWindow) * 100)
    return `Context: ${formatTokenCount(usedTokens)} / ${formatTokenCount(contextWindow)} tokens (${percent}%)`
  }
  if (usedTokens != null) {
    return `${formatTokenCount(usedTokens)} tokens used. Context window unavailable for this model.`
  }
  if (contextWindow != null && hasMessages) {
    return `Token usage not reported yet. Model context window: ${formatTokenCount(contextWindow)} tokens.`
  }
  if (contextWindow != null) {
    return `Model context window: ${formatTokenCount(contextWindow)} tokens`
  }
  return ""
}

export function ContextWindowIndicator({
  usedTokens,
  contextWindow,
  hasMessages = false,
  compact = false,
}: ContextWindowIndicatorProps) {
  const used = cleanTokenCount(usedTokens)
  const limit = cleanTokenCount(contextWindow)
  if (used == null && limit == null) return null

  const fraction = used != null && limit != null ? Math.min(used / limit, 1) : 0
  const percent = Math.round(fraction * 100)
  const hasPercent = used != null && limit != null
  const isNearLimit = hasPercent && fraction >= NEAR_LIMIT
  const isAtLimit = hasPercent && fraction >= AT_LIMIT
  const title = contextLabel(used, limit, hasMessages)
  const dashOffset = RING_CIRCUMFERENCE * (1 - fraction)
  const stroke = isAtLimit
    ? "var(--ui-danger)"
    : isNearLimit
      ? "oklch(0.72 0.17 70)"
      : hasPercent
        ? "var(--ui-accent)"
        : "var(--ui-text-dim)"
  const label = hasPercent
    ? `${percent}%`
    : used != null
      ? `${formatTokenCount(used)} tokens`
      : `${formatTokenCount(limit ?? 0)} context`

  return (
    <span
      data-testid="context-window-indicator"
      title={isNearLimit ? `${title} Approaching context limit.` : title}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 text-[12px] text-[color:var(--ui-text-muted)]",
        isNearLimit && "text-[color:oklch(0.72_0.17_70)]",
        isAtLimit && "text-[color:var(--ui-danger)]"
      )}
    >
      <svg
        width={RING_SIZE}
        height={RING_SIZE}
        viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
        className={cn("block", hasPercent && "-rotate-90")}
        aria-hidden="true"
      >
        <circle
          cx={RING_SIZE / 2}
          cy={RING_SIZE / 2}
          r={RING_RADIUS}
          fill="none"
          stroke="var(--ui-border)"
          strokeWidth={RING_STROKE}
          strokeDasharray={!hasPercent ? "2 2" : undefined}
        />
        {hasPercent && (
          <circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            fill="none"
            stroke={stroke}
            strokeWidth={RING_STROKE}
            strokeDasharray={RING_CIRCUMFERENCE}
            strokeDashoffset={dashOffset}
            strokeLinecap="round"
          />
        )}
      </svg>
      <span className={cn(compact ? "hidden sm:inline" : "inline")}>
        {label}
      </span>
    </span>
  )
}
