import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import type { OpenPullRequest } from "@/lib/api"

export function Diffstat({ pr }: { pr: OpenPullRequest }) {
  if (pr.detailsLoading) return <Skeleton className="h-4 w-20" />
  if (pr.additions === null || pr.deletions === null) return <span>—</span>
  const total = pr.additions + pr.deletions
  return (
    <Stack
      gap="xs"
      className="min-w-24"
      aria-label={`${pr.additions} lines added, ${pr.deletions} lines deleted`}
    >
      <Inline gap="sm" className="font-mono text-label tabular-nums">
        <span className="text-positive">+{pr.additions.toLocaleString()}</span>
        <span className="text-risk">−{pr.deletions.toLocaleString()}</span>
      </Inline>
      <Inline
        bg="muted"
        aria-hidden="true"
        className="h-1 w-20 overflow-hidden rounded-full"
      >
        {total > 0 && (
          <>
            <Box
              render={<span />}
              className="h-full bg-positive"
              style={{ width: `${(pr.additions / total) * 100}%` }}
            />
            <Box
              render={<span />}
              className="h-full bg-risk"
              style={{ width: `${(pr.deletions / total) * 100}%` }}
            />
          </>
        )}
      </Inline>
    </Stack>
  )
}
