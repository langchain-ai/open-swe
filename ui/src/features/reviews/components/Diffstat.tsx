import { Skeleton } from "@/components/ui/skeleton"
import type { OpenPullRequest } from "@/lib/api"

export function Diffstat({ pr }: { pr: OpenPullRequest }) {
  if (pr.detailsLoading) return <Skeleton className="h-4 w-20" />
  if (pr.additions === null || pr.deletions === null) return <span>—</span>
  const total = pr.additions + pr.deletions
  return (
    <div
      className="min-w-24"
      aria-label={`${pr.additions} lines added, ${pr.deletions} lines deleted`}
    >
      <div className="flex gap-2 font-mono text-xs tabular-nums">
        <span className="text-emerald-600 dark:text-emerald-400">
          +{pr.additions.toLocaleString()}
        </span>
        <span className="text-destructive">
          −{pr.deletions.toLocaleString()}
        </span>
      </div>
      <div
        className="mt-1.5 flex h-1 w-20 overflow-hidden rounded-full bg-muted"
        aria-hidden="true"
      >
        {total > 0 && (
          <>
            <span
              className="bg-emerald-500"
              style={{ width: `${(pr.additions / total) * 100}%` }}
            />
            <span
              className="bg-destructive"
              style={{ width: `${(pr.deletions / total) * 100}%` }}
            />
          </>
        )}
      </div>
    </div>
  )
}
