import { useState } from "react"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import { CircularProgress } from "@langchain/macaw-components/CircularProgress"
import { ProgressBar } from "@langchain/macaw-components/ProgressBar"

import {
  formatCost,
  formatTokenCount,
} from "@/features/agents/lib/contextUsage"
import { cn } from "@/lib/utils"

export interface ContextWindowMeterProps {
  usedTokens?: number | null
  contextWindow?: number | null
  model?: string | null
  costUsd?: number | null
}

const OVERLOADED_PERCENTAGE = 90

function cleanTokenCount(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null
}

function formatPercentage(value: number): string {
  return value < 10
    ? `${value.toFixed(1).replace(/\.0$/, "")}%`
    : `${Math.round(value)}%`
}

/** Ring gauge for context usage; the detail panel opens on hover. */
export function ContextWindowMeter({
  usedTokens,
  contextWindow,
  model,
  costUsd,
}: ContextWindowMeterProps) {
  const [open, setOpen] = useState(false)
  const used = cleanTokenCount(usedTokens)
  const limit = cleanTokenCount(contextWindow)
  if (used == null) return null

  const percentage =
    limit != null ? Math.max(0, Math.min(100, (used / limit) * 100)) : 0
  const hasPercentage = limit != null
  const isOverloaded = hasPercentage && percentage >= OVERLOADED_PERCENTAGE
  const usageVariant = isOverloaded ? "error" : "brand"
  const label = hasPercentage
    ? `Context window ${formatPercentage(percentage)} used`
    : `Context window ${formatTokenCount(used)} tokens`

  return (
    <HoverCard
      closeDelay={0}
      onOpenChange={setOpen}
      open={open}
      openDelay={150}
    >
      <HoverCardTrigger asChild>
        <button
          aria-label={label}
          className={cn(
            "inline-flex size-7 cursor-pointer items-center justify-center rounded-full border border-transparent text-secondary transition-colors outline-none",
            "hover:bg-surface-level-1-hover data-[state=open]:bg-surface-level-1-hover",
            "focus-visible:ring-2 focus-visible:ring-focus"
          )}
          data-testid="context-window-indicator"
          // Touch screens never hover, so a tap opens the detail too.
          onClick={() => setOpen(true)}
          type="button"
        >
          <CircularProgress
            segments={
              hasPercentage
                ? [{ variant: usageVariant, percentage: percentage / 100 }]
                : []
            }
            size={20}
            strokeWidth={3}
          />
        </button>
      </HoverCardTrigger>
      <HoverCardContent
        align="end"
        className="w-64 p-space-3 text-left whitespace-normal"
        side="top"
      >
        <div className="flex flex-col gap-space-2">
          <div className="flex items-center justify-between gap-space-3">
            <div className="text-xs font-medium text-secondary">
              Context window
            </div>
            <div className="text-xxs text-tertiary tabular-nums">
              {hasPercentage ? (
                <>
                  <span>{formatPercentage(percentage)}</span>
                  <span className="mx-space-1">·</span>
                  <span>
                    {formatTokenCount(used)}/{formatTokenCount(limit)}
                  </span>
                </>
              ) : (
                formatTokenCount(used)
              )}
            </div>
          </div>
          {hasPercentage && (
            <ProgressBar
              aria-label="Context window usage"
              color={usageVariant}
              size="sm"
              value={percentage}
            />
          )}
          {!hasPercentage && (
            <p className="text-xxs leading-4 text-tertiary">
              The context window for this model was not reported.
            </p>
          )}
          {(model || costUsd != null) && (
            <div className="flex items-center justify-between gap-space-3 text-xxs text-tertiary">
              <span className="truncate">{model}</span>
              {costUsd != null && (
                <span className="tabular-nums">
                  {formatCost(costUsd)} total
                </span>
              )}
            </div>
          )}
          {isOverloaded && (
            <p className="text-xxs leading-4 font-medium text-error-secondary">
              Approaching the context limit — start a new thread if replies
              degrade.
            </p>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
