import type { GuidanceKind, GuidancePoint } from "@/lib/api"
import { cn } from "@/lib/utils"
import { dateLabel } from "../lib/dateLabel"

const kindLabels: Record<GuidanceKind, string> = {
  correction: "Corrected",
  constraint: "Ruled out",
  direction: "Redirected",
  preference: "Preference",
}

const kindTones: Record<GuidanceKind, string> = {
  correction: "text-destructive",
  constraint: "text-amber-700 dark:text-amber-400",
  direction: "text-sky-700 dark:text-sky-400",
  preference: "text-muted-foreground",
}

/**
 * Oldest first, so a reader follows the steering in the order it happened. A
 * point whose quote matched no message has no time to place it by, so it sorts
 * to the end rather than to the beginning of the day.
 */
function inOrder(points: Array<GuidancePoint>): Array<GuidancePoint> {
  const at = (point: GuidancePoint) => point.occurred_at || "￿"
  return [...points].sort((a, b) => at(a).localeCompare(at(b)))
}

function Point({ point }: { point: GuidancePoint }) {
  return (
    <li className="border-l-2 border-border pl-3">
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
        <span className={cn("font-medium", kindTones[point.kind])}>
          {kindLabels[point.kind]}
        </span>
        {point.author && <span>{point.author}</span>}
        {point.occurred_at && (
          <span className="tabular-nums">{dateLabel(point.occurred_at)}</span>
        )}
        <span className="min-w-0 truncate font-mono" title={point.file}>
          {point.file}
          {point.start_line !== null && `:${point.start_line}`}
        </span>
      </div>
      <p className="mt-0.5 text-sm text-foreground">{point.summary}</p>
      <p className="mt-1 text-xs whitespace-pre-wrap text-muted-foreground italic">
        “{point.quote}”
      </p>
    </li>
  )
}

export function GuidancePointList({
  points,
}: {
  points: Array<GuidancePoint>
}) {
  return (
    <ul className="space-y-2.5">
      {inOrder(points).map((point, index) => (
        <Point key={`${point.file}:${index}`} point={point} />
      ))}
    </ul>
  )
}

/**
 * The points where the author steered Open SWE, as the reviewer found them in
 * the final change. Renders nothing until a review has recorded some.
 */
export function AuthorGuidanceCard({
  points,
  className,
}: {
  points: Array<GuidancePoint>
  className?: string
}) {
  if (!points.length) return null
  return (
    <section
      aria-label="How the author steered this PR"
      className={cn("rounded-lg border border-border bg-card p-4", className)}
    >
      <div className="mb-2.5 flex items-baseline gap-2">
        <h3 className="text-xs font-medium text-foreground">
          How the author steered this PR
        </h3>
        <span className="text-xs text-muted-foreground tabular-nums">
          {points.length} {points.length === 1 ? "point" : "points"}
        </span>
      </div>
      <GuidancePointList points={points} />
    </section>
  )
}
