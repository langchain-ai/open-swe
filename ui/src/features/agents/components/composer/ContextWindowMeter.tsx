import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { formatTokenCount } from "@/features/agents/lib/contextUsage"
import { cn } from "@/lib/utils"

export interface ContextWindowMeterProps {
  usedTokens?: number | null
  contextWindow?: number | null
}

const RADIUS = 9.75
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
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
}: ContextWindowMeterProps) {
  const used = cleanTokenCount(usedTokens)
  const limit = cleanTokenCount(contextWindow)
  if (used == null) return null

  const percentage =
    limit != null ? Math.max(0, Math.min(100, (used / limit) * 100)) : 0
  const hasPercentage = limit != null
  const isOverloaded = hasPercentage && percentage >= OVERLOADED_PERCENTAGE
  const usageColor = isOverloaded
    ? "var(--color-destructive)"
    : "color-mix(in oklab, var(--color-muted-foreground) 72%, transparent)"
  const label = hasPercentage
    ? `Context window ${formatPercentage(percentage)} used`
    : `Context window ${formatTokenCount(used)} tokens`

  return (
    <Popover>
      <PopoverTrigger
        closeDelay={0}
        delay={150}
        openOnHover
        render={
          <button
            aria-label={label}
            className={cn(
              "inline-flex size-7 cursor-pointer items-center justify-center rounded-full border border-transparent text-muted-foreground transition-colors outline-none",
              "hover:bg-accent data-[pressed]:bg-accent",
              "focus-visible:ring-2 focus-visible:ring-ring"
            )}
            data-testid="context-window-indicator"
            type="button"
          >
            <span className="relative flex size-5 items-center justify-center">
              <svg
                aria-hidden="true"
                className="absolute inset-0 size-full -rotate-90 transform-gpu"
                viewBox="0 0 24 24"
              >
                <circle
                  cx="12"
                  cy="12"
                  fill="none"
                  r={RADIUS}
                  stroke="color-mix(in oklab, var(--color-muted-foreground) 24%, transparent)"
                  strokeDasharray={hasPercentage ? undefined : "3 3"}
                  strokeWidth="3"
                />
                {hasPercentage && (
                  <circle
                    className="transition-[stroke-dashoffset,stroke] duration-500 ease-out motion-reduce:transition-none"
                    cx="12"
                    cy="12"
                    fill="none"
                    r={RADIUS}
                    stroke={usageColor}
                    strokeDasharray={CIRCUMFERENCE}
                    strokeDashoffset={CIRCUMFERENCE * (1 - percentage / 100)}
                    strokeLinecap="round"
                    strokeWidth="3"
                  />
                )}
              </svg>
            </span>
          </button>
        }
      />
      <PopoverPopup
        align="end"
        className="w-64 max-w-none text-left whitespace-normal"
        side="top"
        tooltipStyle
      >
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-medium text-muted-foreground">
              Context window
            </div>
            <div className="text-[11px] text-muted-foreground/70 tabular-nums">
              {hasPercentage ? (
                <>
                  <span>{formatPercentage(percentage)}</span>
                  <span className="mx-1">·</span>
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
            <div
              aria-label="Context window usage"
              aria-valuemax={100}
              aria-valuemin={0}
              aria-valuenow={Math.round(percentage)}
              className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60"
              role="progressbar"
            >
              <div
                className="h-full rounded-full transition-[width,background-color] duration-500 ease-out motion-reduce:transition-none"
                style={{ backgroundColor: usageColor, width: `${percentage}%` }}
              />
            </div>
          )}
          {!hasPercentage && (
            <p className="text-[11px] leading-4 text-muted-foreground/70">
              The context window for this model was not reported.
            </p>
          )}
          {isOverloaded && (
            <p className="text-[11px] leading-4 font-medium text-destructive">
              Approaching the context limit — start a new thread if replies
              degrade.
            </p>
          )}
        </div>
      </PopoverPopup>
    </Popover>
  )
}
