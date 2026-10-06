import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { ChevronRight, XCircle } from "@/components/glyphs"
import type { OpenPullRequest } from "@/lib/api"

const inlineLimit = 3

const disclosure =
  "group/checks [&_summary]:flex [&_summary]:w-fit [&_summary]:cursor-pointer [&_summary]:list-none [&_summary]:items-center [&_summary]:gap-1 [&_summary::-webkit-details-marker]:hidden"

function DisclosureMark() {
  return (
    <Icon
      icon={ChevronRight}
      size="sm"
      className="transition-transform duration-fast ease-out-quint group-open/checks:rotate-90 motion-reduce:transition-none"
    />
  )
}

export function PullRequestChecks({ pr }: { pr: OpenPullRequest }) {
  if (pr.failingChecks.length === 0 && pr.pendingChecks.length === 0)
    return null
  const overflow = pr.failingChecks.slice(inlineLimit)
  return (
    <Stack gap="sm" className="text-label">
      {pr.failingChecks.length > 0 && (
        <Inline gap="xs" wrap>
          {pr.failingChecks.slice(0, inlineLimit).map((name, index) => (
            <Badge key={`${name}-${index}`} tier="quiet" tone="risk">
              <Icon icon={XCircle} size="sm" />
              {name}
            </Badge>
          ))}
        </Inline>
      )}
      {overflow.length > 0 && (
        <details className={`${disclosure} text-risk`}>
          <summary>
            <DisclosureMark />+{overflow.length} more
          </summary>
          <Stack render={<ul />} gap="xs" className="mt-1 pl-5">
            {overflow.map((name, index) => (
              <li key={`${name}-${index}`}>{name}</li>
            ))}
          </Stack>
        </details>
      )}
      {pr.pendingChecks.length > 0 && (
        <details className={`${disclosure} text-ink-subtle`}>
          <summary>
            <DisclosureMark />
            {pr.pendingChecks.length} pending
          </summary>
          <Stack render={<ul />} gap="xs" className="mt-1 pl-5">
            {pr.pendingChecks.map((name, index) => (
              <li key={`${name}-${index}`}>{name}</li>
            ))}
          </Stack>
        </details>
      )}
    </Stack>
  )
}
