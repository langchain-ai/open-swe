import { cn } from "@/lib/utils"

/** `+N −N` line counts, optionally with a proportional bar underneath. */
export function DiffStat({
  additions,
  deletions,
  bar = false,
  className,
}: {
  additions: number
  deletions: number
  bar?: boolean
  className?: string
}) {
  const total = additions + deletions
  const counts = (
    <span
      className={cn(
        "inline-flex gap-1.5 font-mono tabular-nums",
        !bar && className
      )}
    >
      <span className="text-success-foreground">
        +{additions.toLocaleString()}
      </span>
      <span className="text-destructive">−{deletions.toLocaleString()}</span>
    </span>
  )
  const label = `${additions} lines added, ${deletions} lines deleted`
  if (!bar) {
    return (
      <span aria-label={label} className="inline-flex" role="img">
        {counts}
      </span>
    )
  }
  return (
    <div aria-label={label} className={cn("min-w-24", className)} role="img">
      {counts}
      <div
        aria-hidden="true"
        className="mt-1.5 flex h-1 w-20 overflow-hidden rounded-full bg-muted"
      >
        {total > 0 && (
          <>
            <span
              className="bg-success"
              style={{ width: `${(additions / total) * 100}%` }}
            />
            <span
              className="bg-destructive"
              style={{ width: `${(deletions / total) * 100}%` }}
            />
          </>
        )}
      </div>
    </div>
  )
}
