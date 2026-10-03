import { GitMergeIcon, GitPullRequestIcon } from "@phosphor-icons/react"
import type { Icon } from "@phosphor-icons/react"
import type * as React from "react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export type PrState = "draft" | "open" | "merged" | "closed"

export const PR_STATE_META: Record<
  PrState,
  {
    icon: Icon
    label: string
    iconClassName: string
    badge: React.ComponentProps<typeof Badge>["variant"]
  }
> = {
  draft: {
    icon: GitPullRequestIcon,
    label: "Draft pull request",
    iconClassName: "text-muted-foreground/70",
    badge: "muted",
  },
  open: {
    icon: GitPullRequestIcon,
    label: "Open pull request",
    iconClassName: "text-success-foreground",
    badge: "success",
  },
  merged: {
    icon: GitMergeIcon,
    label: "Merged pull request",
    iconClassName: "text-merged-foreground",
    badge: "merged",
  },
  closed: {
    icon: GitPullRequestIcon,
    label: "Closed pull request",
    iconClassName: "text-destructive",
    badge: "destructive",
  },
}

export function toPrState(value: string): PrState {
  return value in PR_STATE_META ? (value as PrState) : "open"
}

export function PrStateBadge({
  state,
  icon = false,
  className,
}: {
  state: PrState
  icon?: boolean
  className?: string
}) {
  const meta = PR_STATE_META[state]
  const Glyph = meta.icon
  return (
    <Badge className={cn("capitalize", className)} variant={meta.badge}>
      {icon && <Glyph data-icon="inline-start" />}
      {state}
    </Badge>
  )
}

export function PrStateIcon({
  state,
  className,
}: {
  state: PrState
  className?: string
}) {
  const meta = PR_STATE_META[state]
  const Glyph = meta.icon
  return (
    <Glyph
      aria-label={meta.label}
      className={cn("size-3.5 shrink-0", meta.iconClassName, className)}
      role="img"
    />
  )
}
