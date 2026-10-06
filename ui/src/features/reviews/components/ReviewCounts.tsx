import { Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { Bug, Flag } from "@/components/glyphs"
import type { ReviewCounts as Counts } from "@/lib/api"

export function ReviewCounts({
  counts,
  withLabels = false,
}: {
  counts: Counts
  withLabels?: boolean
}) {
  return (
    <>
      <Inline
        render={<span />}
        gap="xs"
        ink={counts.bugs > 0 ? "risk" : "ink-subtle"}
        className="font-mono tabular-nums"
      >
        <Icon icon={Bug} size="sm" />
        {counts.bugs}
        {withLabels && " bugs"}
      </Inline>
      <Inline
        render={<span />}
        gap="xs"
        ink="ink-subtle"
        className="font-mono tabular-nums"
      >
        <Icon icon={Flag} size="sm" />
        {counts.flags}
        {withLabels && " flags"}
      </Inline>
    </>
  )
}
