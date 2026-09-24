import type { GuidancePoint } from "@/lib/api"
import { cn } from "@/lib/utils"

/** The summary reads as the point; the author's own words are a click away. */
function Point({
  point,
  showAuthor,
}: {
  point: GuidancePoint
  showAuthor: boolean
}) {
  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-baseline gap-2">
          <span
            aria-hidden="true"
            className="shrink-0 text-xs text-muted-foreground transition-transform group-open:rotate-90"
          >
            ›
          </span>
          <span className="min-w-0 flex-1 text-sm text-foreground">
            {point.summary}
          </span>
          {showAuthor && point.author && (
            <span className="shrink-0 text-xs text-muted-foreground">
              {point.author}
            </span>
          )}
        </summary>
        <p className="mt-1 ml-5 border-l-2 border-border pl-3 text-xs whitespace-pre-wrap text-muted-foreground italic">
          {point.quote}
        </p>
      </details>
    </li>
  )
}

/** Ordered by the server: oldest steering first, unattributed points last. */
export function GuidancePointList({
  points,
}: {
  points: Array<GuidancePoint>
}) {
  // One person steering is the norm, and repeating their name on every row says
  // nothing. It only earns its place when the points have more than one source.
  const showAuthor =
    new Set(points.map((point) => point.author).filter(Boolean)).size > 1
  return (
    <ul className="space-y-2">
      {points.map((point, index) => (
        <Point
          key={`${point.author}:${index}`}
          point={point}
          showAuthor={showAuthor}
        />
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
      aria-label="Human input"
      className={cn("rounded-lg border border-border bg-card p-4", className)}
    >
      <div className="mb-2.5 flex items-baseline gap-2">
        <h3 className="text-xs font-medium text-foreground">
          Human input
        </h3>
        <span className="text-xs text-muted-foreground tabular-nums">
          {points.length} {points.length === 1 ? "point" : "points"}
        </span>
      </div>
      <GuidancePointList points={points} />
    </section>
  )
}
