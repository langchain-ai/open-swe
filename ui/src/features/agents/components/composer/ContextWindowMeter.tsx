import { Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import { Progress } from "@langchain/gtm-platform-design-system/ui/progress"
import { formatTokenCount } from "@/features/agents/lib/contextUsage"

export interface ContextWindowMeterProps {
  usedTokens?: number | null
  contextWindow?: number | null
}

const RADIUS = 9
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

/** Ring gauge for context usage; the reading opens on hover. */
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
          <Button
            aria-label={label}
            className="rounded-full text-ink-subtle"
            data-testid="context-window-indicator"
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <svg
              aria-hidden="true"
              className="size-4 -rotate-90"
              viewBox="0 0 24 24"
            >
              <circle
                cx="12"
                cy="12"
                fill="none"
                r={RADIUS}
                stroke="var(--gtm-line-strong)"
                strokeDasharray={hasPercentage ? undefined : "3 3"}
                strokeWidth="3"
              />
              {hasPercentage && (
                <circle
                  className="transition-[stroke-dashoffset] duration-fast ease-out-quint motion-reduce:transition-none"
                  cx="12"
                  cy="12"
                  fill="none"
                  r={RADIUS}
                  stroke={
                    isOverloaded ? "var(--gtm-risk)" : "var(--gtm-ink-subtle)"
                  }
                  strokeDasharray={CIRCUMFERENCE}
                  strokeDashoffset={CIRCUMFERENCE * (1 - percentage / 100)}
                  strokeLinecap="round"
                  strokeWidth="3"
                />
              )}
            </svg>
          </Button>
        }
      />
      <PopoverContent align="end" className="w-64" side="top">
        <Stack gap="sm">
          <Inline justify="between" gap="md">
            <span className="text-label font-medium text-ink">
              Context window
            </span>
            <span className="font-mono text-meta text-ink-subtle tabular-nums">
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
            </span>
          </Inline>
          {hasPercentage ? (
            <Progress
              label="Context window usage"
              value={Math.round(percentage)}
            />
          ) : (
            <p className="text-meta text-ink-subtle">
              The context window for this model was not reported.
            </p>
          )}
          {isOverloaded && (
            <p className="text-meta font-medium text-risk">
              Approaching the context limit — start a new thread if replies
              degrade.
            </p>
          )}
        </Stack>
      </PopoverContent>
    </Popover>
  )
}
