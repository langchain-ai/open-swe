import { DiffStat } from "@/components/DiffStat"
import { Skeleton } from "@/components/ui/skeleton"
import type { OpenPullRequest } from "@/lib/api"

export function Diffstat({ pr }: { pr: OpenPullRequest }) {
  if (pr.detailsLoading) return <Skeleton className="h-4 w-20" />
  if (pr.additions === null || pr.deletions === null) return <span>—</span>
  return (
    <DiffStat
      additions={pr.additions}
      bar
      className="text-xs"
      deletions={pr.deletions}
    />
  )
}
