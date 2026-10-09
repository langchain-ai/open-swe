import { FlagRegularIcon } from "@langchain/macaw-components/icons"
import { BugBeetleIcon } from "@phosphor-icons/react/dist/ssr/BugBeetle"

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
          counts.bugs > 0 ? "text-error-secondary" : "text-secondary"
        )}
      >
        <BugBeetleIcon
          aria-hidden="true"
          weight="regular"
          className="size-3.5"
        />
        {counts.bugs}
        {withLabels && " bugs"}
      </span>
      <span className="inline-flex items-center gap-1 text-secondary">
        <FlagRegularIcon aria-hidden="true" className="size-3.5" />
        {counts.flags}
        {withLabels && " flags"}
      </span>
    </>
  )
}
