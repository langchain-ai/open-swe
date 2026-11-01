import { Badge, type BadgeProps } from "@langchain/macaw-components/Badge"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"

import type { PullRequestSearchResult } from "@/lib/api"
import { cn } from "@/lib/utils"

export type PullRequestState = PullRequestSearchResult["state"]

const stateColor: Record<PullRequestState, BadgeProps["color"]> = {
  open: "success",
  draft: "secondary",
  merged: "primary",
  closed: "error",
}

/** GitHub's lifecycle for a PR, in the colors GitHub uses for it. */
export function PullRequestStateBadge({
  state,
  className,
}: {
  state: PullRequestState
  className?: string
}) {
  return (
    <Badge
      size="xs"
      color={stateColor[state]}
      leftDecorator={state === "merged" ? GitMergeIcon : GitPullRequestIcon}
      className={cn("shrink-0 capitalize", className)}
    >
      {state}
    </Badge>
  )
}
