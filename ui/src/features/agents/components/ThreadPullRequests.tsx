import { useState } from "react"
import { ChevronDown, ChevronUp, GitPullRequest } from "lucide-react"

import type { AgentPullRequest } from "@/features/agents/lib/types"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

const PR_STATE_STYLES: Record<AgentPullRequest["state"], string> = {
  draft: "bg-muted text-muted-foreground",
  open: "bg-success/15 text-success-foreground",
  merged: "bg-info/15 text-info-foreground",
  closed: "bg-destructive/10 text-destructive",
}

function relativeAge(value: string | null): string {
  if (!value) return ""
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return ""
  const elapsedSeconds = Math.max(
    0,
    Math.floor((Date.now() - timestamp) / 1000)
  )
  if (elapsedSeconds < 60) return "now"
  const elapsedMinutes = Math.floor(elapsedSeconds / 60)
  if (elapsedMinutes < 60) return `${elapsedMinutes}m ago`
  const elapsedHours = Math.floor(elapsedMinutes / 60)
  if (elapsedHours < 24) return `${elapsedHours}h ago`
  const elapsedDays = Math.floor(elapsedHours / 24)
  if (elapsedDays < 30) return `${elapsedDays}d ago`
  const elapsedMonths = Math.floor(elapsedDays / 30)
  if (elapsedMonths < 12) return `${elapsedMonths}mo ago`
  return `${Math.floor(elapsedMonths / 12)}y ago`
}

function PullRequestHoverCard({
  pullRequest,
}: {
  pullRequest: AgentPullRequest
}) {
  const age = relativeAge(pullRequest.createdAt)
  const authorInitial = pullRequest.author?.slice(0, 1).toUpperCase() || "?"

  return (
    <div
      data-testid={`pr-hover-card-${pullRequest.repoFullName}-${pullRequest.number}`}
      className="w-96 max-w-[calc(100vw-2rem)] space-y-3 p-1"
    >
      <div className="flex items-center gap-2 text-sm">
        <span
          className={cn(
            "rounded-full px-2.5 py-1 text-xs font-medium capitalize",
            PR_STATE_STYLES[pullRequest.state]
          )}
        >
          {pullRequest.state}
        </span>
        <span className="min-w-0 truncate text-muted-foreground">
          {pullRequest.repoFullName} #{pullRequest.number}
        </span>
        {age && (
          <time
            dateTime={pullRequest.createdAt ?? undefined}
            suppressHydrationWarning
            className="ml-auto shrink-0 text-muted-foreground"
          >
            {age}
          </time>
        )}
      </div>
      <p className="text-base leading-snug font-medium text-foreground">
        {pullRequest.title}
      </p>
      <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <span className="truncate">{pullRequest.baseRef}</span>
        <span aria-hidden="true">←</span>
        <span className="truncate">{pullRequest.headRef}</span>
      </div>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Avatar size="sm">
          {pullRequest.authorAvatarUrl && (
            <AvatarImage src={pullRequest.authorAvatarUrl} alt="" />
          )}
          <AvatarFallback>{authorInitial}</AvatarFallback>
        </Avatar>
        <span className="min-w-0 truncate">
          {pullRequest.author ?? "Unknown author"}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2">
          <span className="text-success-foreground">
            +{pullRequest.diffStats.additions}
          </span>
          <span className="text-destructive">
            -{pullRequest.diffStats.deletions}
          </span>
          <span>
            {pullRequest.diffStats.files} file
            {pullRequest.diffStats.files === 1 ? "" : "s"}
          </span>
        </span>
      </div>
    </div>
  )
}

function PullRequestLink({ pullRequest }: { pullRequest: AgentPullRequest }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <a
            href={pullRequest.url}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${pullRequest.repoFullName} pull request #${pullRequest.number}`}
            className="group flex min-w-0 items-center gap-2 rounded-lg border border-border/70 bg-card/80 px-3 py-2 text-xs shadow-sm transition-colors hover:border-border hover:bg-accent/70"
          />
        }
      >
        <GitPullRequest className="size-4 shrink-0 text-success-foreground" />
        <span className="shrink-0 font-medium text-success-foreground">
          #{pullRequest.number}
        </span>
        <span className="min-w-0 truncate text-muted-foreground">
          {pullRequest.repoFullName}
        </span>
        <span className="hidden min-w-0 truncate text-muted-foreground/70 sm:block">
          {pullRequest.headRef}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <span className="text-success-foreground">
            +{pullRequest.diffStats.additions}
          </span>
          <span className="text-destructive">
            -{pullRequest.diffStats.deletions}
          </span>
          <span
            className={cn(
              "rounded-full px-2 py-0.5 font-medium capitalize",
              PR_STATE_STYLES[pullRequest.state]
            )}
          >
            {pullRequest.state}
          </span>
        </span>
      </TooltipTrigger>
      <TooltipPopup
        variant="glass"
        side="top"
        align="start"
        sideOffset={8}
        className="rounded-xl p-3 shadow-2xl"
      >
        <PullRequestHoverCard pullRequest={pullRequest} />
      </TooltipPopup>
    </Tooltip>
  )
}

export function ThreadPullRequests({
  pullRequests,
}: {
  pullRequests: Array<AgentPullRequest>
}) {
  const [expanded, setExpanded] = useState(false)
  if (pullRequests.length === 0) return null

  const visiblePullRequests = expanded ? pullRequests : pullRequests.slice(-1)
  const hiddenCount = pullRequests.length - 1

  return (
    <div data-testid="thread-pull-requests" className="space-y-1.5 pb-2">
      {visiblePullRequests.map((pullRequest) => (
        <PullRequestLink
          key={`${pullRequest.repoFullName}#${pullRequest.number}`}
          pullRequest={pullRequest}
        />
      ))}
      {hiddenCount > 0 && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {expanded ? (
            <ChevronUp className="size-3.5" />
          ) : (
            <ChevronDown className="size-3.5" />
          )}
          {expanded ? "Show less" : `Show ${hiddenCount} more`}
        </button>
      )}
    </div>
  )
}
