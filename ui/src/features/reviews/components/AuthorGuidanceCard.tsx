import { useQuery } from "@tanstack/react-query"

import type { AuthorGuidance, GuidanceKind, GuidancePoint } from "@/lib/api"
import { api } from "@/lib/api"
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

export function useAuthorGuidance(owner: string, repo: string, number: number) {
  return useQuery({
    queryKey: ["author-guidance", owner, repo, number],
    queryFn: () => api.getAuthorGuidance(owner, repo, number),
    staleTime: 5 * 60_000,
    retry: false,
  })
}

function Point({ point }: { point: GuidancePoint }) {
  return (
    <li className="border-l-2 border-border pl-3">
      <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
        <span className={cn("font-medium", kindTones[point.kind])}>
          {kindLabels[point.kind]}
        </span>
        {point.author && <span>{point.author}</span>}
        {point.occurred_at && (
          <span className="tabular-nums">{dateLabel(point.occurred_at)}</span>
        )}
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
      {points.map((point, index) => (
        <Point key={`${point.kind}:${index}`} point={point} />
      ))}
    </ul>
  )
}

export function guidanceCount(guidance: AuthorGuidance): string {
  return `${guidance.points.length} of ${guidance.follow_up_count} ${
    guidance.follow_up_count === 1 ? "message" : "messages"
  }`
}

/**
 * The points where the author steered Open SWE, for a PR Open SWE wrote.
 *
 * The rows are written by the reviewer run, so they are absent until a review
 * has happened; the card renders nothing rather than claiming the author never
 * intervened.
 */
export function AuthorGuidanceCard({
  guidance,
  className,
}: {
  guidance: AuthorGuidance
  className?: string
}) {
  if (!guidance.points.length) return null
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
          {guidanceCount(guidance)}
        </span>
      </div>
      <GuidancePointList points={guidance.points} />
    </section>
  )
}
