import { BugBeetleIcon, FlagIcon } from "@phosphor-icons/react"

import type { ReviewCounts as Counts } from "@/lib/api"
import { cn } from "@/lib/utils"

export function ReviewCounts({
  counts,
  withLabels = false,
}: {
  counts: Counts
  withLabels?: boolean
}) {
  return (
    <>
      <span
        className={cn(
          "inline-flex items-center gap-1",
          counts.bugs > 0 ? "text-destructive" : "text-muted-foreground"
        )}
      >
        <BugBeetleIcon aria-hidden="true" className="size-3.5" />
        {counts.bugs}
        {withLabels && " bugs"}
      </span>
      <span className="inline-flex items-center gap-1 text-muted-foreground">
        <FlagIcon aria-hidden="true" className="size-3.5" />
        {counts.flags}
        {withLabels && " flags"}
      </span>
    </>
  )
}
