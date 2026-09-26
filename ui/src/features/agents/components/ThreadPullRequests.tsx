import { useState } from "react"
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  GitPullRequest,
  MessageCircle,
  Wrench,
} from "lucide-react"

import type {
  AgentPullRequest,
  AgentPullRequestHealth,
} from "@/features/agents/lib/types"
import { DiffStat } from "@/components/DiffStat"
import { PrStateBadge } from "@/components/PrState"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  HoverCard,
  HoverCardPopup,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { cn } from "@/lib/utils"

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

function pullRequestState(
  pullRequest: AgentPullRequest,
  health: AgentPullRequestHealth | undefined
): AgentPullRequest["state"] {
  if (!health?.statusAvailable || !health.state) return pullRequest.state
  if (health.state === "open" && health.isDraft) return "draft"
  return health.state
}

function hasActionableIssues(
  pullRequest: AgentPullRequest,
  health: AgentPullRequestHealth | undefined
): boolean {
  const state = pullRequestState(pullRequest, health)
  if (state !== "open" && state !== "draft") return false
  return Boolean(
    health &&
    (health.failingChecks.length > 0 ||
      (health.unresolvedReviewThreadCount ?? 0) > 0 ||
      health.mergeConflictState === "conflicting")
  )
}

function pullRequestTone(
  pullRequest: AgentPullRequest,
  health: AgentPullRequestHealth | undefined
): string {
  const state = pullRequestState(pullRequest, health)
  if (state === "merged") return "text-info-foreground"
  if (state === "closed") return "text-destructive"
  if (hasActionableIssues(pullRequest, health)) return "text-destructive"
  if (state === "draft") return "text-muted-foreground"
  if (
    !health?.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable ||
    health.mergeConflictState !== "mergeable"
  ) {
    return "text-muted-foreground"
  }
  if (
    (health.pendingCheckCount ?? 0) > 0 ||
    (health.inconclusiveCheckCount ?? 0) > 0
  ) {
    return "text-warning-foreground"
  }
  return "text-success-foreground"
}

function healthKey(repoFullName: string, number: number): string {
  return `${repoFullName}#${number}`
}

function HealthSummary({
  health,
}: {
  health: AgentPullRequestHealth | undefined
}) {
  if (!health) return null
  const failingCount = health.failingChecks.length
  const commentCount = health.unresolvedReviewThreadCount ?? 0
  return (
    <>
      {failingCount > 0 && (
        <Badge variant="destructive">
          {failingCount} check{failingCount === 1 ? "" : "s"}
        </Badge>
      )}
      {commentCount > 0 && (
        <Badge variant="warning">
          {commentCount} comment{commentCount === 1 ? "" : "s"}
        </Badge>
      )}
      {health.mergeConflictState === "conflicting" && (
        <Badge variant="destructive">Conflict</Badge>
      )}
      {(health.pendingCheckCount ?? 0) > 0 && (
        <Badge variant="warning">{health.pendingCheckCount} pending</Badge>
      )}
    </>
  )
}

function HealthDetails({
  health,
  unavailable,
}: {
  health: AgentPullRequestHealth | undefined
  unavailable: boolean
}) {
  if (unavailable) {
    return (
      <p className="border-t border-border/70 pt-3 text-xs text-muted-foreground">
        GitHub health is unavailable. This PR is not marked clean.
      </p>
    )
  }
  if (!health) {
    return <p className="text-xs text-muted-foreground">Loading PR health…</p>
  }
  const healthUnavailable =
    !health.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable
  return (
    <div className="space-y-3 border-t border-border/70 pt-3">
      {health.mergeConflictState === "conflicting" && (
        <div className="flex items-start gap-2 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>This branch has merge conflicts.</span>
        </div>
      )}
      {health.failingChecks.length > 0 && (
        <div className="space-y-1.5" data-testid="pr-failing-checks">
          <p className="text-xs font-medium text-foreground">
            Failing checks ({health.failingChecks.length})
          </p>
          {health.failingChecks.map((check, index) => {
            const label = check.name || "Unnamed check"
            const content = (
              <>
                <AlertTriangle className="size-3.5 shrink-0 text-destructive" />
                <span className="min-w-0 flex-1 truncate">{label}</span>
                {check.conclusion && (
                  <span className="shrink-0 text-muted-foreground">
                    {check.conclusion.replaceAll("_", " ")}
                  </span>
                )}
              </>
            )
            return (
              <div
                key={`${label}-${index}`}
                className="flex items-center gap-2 px-1 py-0.5 text-xs text-foreground"
              >
                {content}
              </div>
            )
          })}
        </div>
      )}
      {(health.unresolvedReviewThreadCount ?? 0) > 0 && (
        <div className="space-y-2" data-testid="pr-unresolved-comments">
          <p className="text-xs font-medium text-foreground">
            Unresolved comments ({health.unresolvedReviewThreadCount})
          </p>
          {health.unresolvedReviewThreads.map((thread, index) => {
            const location = thread.path
              ? `${thread.path}${thread.line ? `:${thread.line}` : ""}`
              : "Pull request"
            const content = (
              <>
                <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <MessageCircle className="size-3 shrink-0" />
                  <span>{thread.author ?? "Unknown author"}</span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{location}</span>
                </div>
                <p className="line-clamp-2 text-xs text-foreground">
                  {thread.body || "No comment text"}
                </p>
              </>
            )
            return (
              <div
                key={`${location}-${index}`}
                className="space-y-1 px-1 py-0.5"
              >
                {content}
              </div>
            )
          })}
        </div>
      )}
      {healthUnavailable && (
        <p className="text-xs text-muted-foreground">
          Some GitHub health details are unavailable. This PR is not marked
          clean.
        </p>
      )}
    </div>
  )
}

export function PullRequestHoverCard({
  pullRequest,
  health,
  healthUnavailable,
}: {
  pullRequest: AgentPullRequest
  health: AgentPullRequestHealth | undefined
  healthUnavailable: boolean
}) {
  const age = relativeAge(pullRequest.createdAt)
  const authorInitial = pullRequest.author?.slice(0, 1).toUpperCase() || "?"
  const state = pullRequestState(pullRequest, health)

  return (
    <div
      data-testid={`pr-hover-card-${pullRequest.repoFullName}-${pullRequest.number}`}
      className="w-96 max-w-[calc(100vw-2rem)] space-y-3 p-1"
    >
      <div className="flex items-center gap-2 text-sm">
        <PrStateBadge state={state} />
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
          <DiffStat
            additions={pullRequest.diffStats.additions}
            deletions={pullRequest.diffStats.deletions}
          />
          <span>
            {pullRequest.diffStats.files} file
            {pullRequest.diffStats.files === 1 ? "" : "s"}
          </span>
        </span>
      </div>
      <HealthDetails health={health} unavailable={healthUnavailable} />
    </div>
  )
}

function PullRequestLink({
  pullRequest,
  health,
  healthUnavailable,
  onFix,
  fixDisabled,
}: {
  pullRequest: AgentPullRequest
  health: AgentPullRequestHealth | undefined
  healthUnavailable: boolean
  onFix?: (pullRequest: AgentPullRequest) => Promise<void> | void
  fixDisabled: boolean
}) {
  const [fixing, setFixing] = useState(false)
  const [fixFailed, setFixFailed] = useState(false)
  const state = pullRequestState(pullRequest, health)
  const tone = pullRequestTone(pullRequest, health)
  const actionable = hasActionableIssues(pullRequest, health)
  const handleFix = async () => {
    if (!health || !onFix) return
    setFixFailed(false)
    setFixing(true)
    try {
      await onFix(pullRequest)
    } catch {
      setFixFailed(true)
    } finally {
      setFixing(false)
    }
  }

  return (
    <div className="flex min-w-0 items-stretch gap-1.5">
      <HoverCard>
        <HoverCardTrigger
          render={
            <a
              href={pullRequest.url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${pullRequest.repoFullName} pull request #${pullRequest.number}`}
              data-testid={`pr-summary-${pullRequest.repoFullName}-${pullRequest.number}`}
              data-pr-tone={tone}
              className="group flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-border/70 bg-card/80 px-3 py-2 text-xs shadow-sm transition-colors hover:border-border hover:bg-accent/70"
            />
          }
        >
          <GitPullRequest className={cn("size-4 shrink-0", tone)} />
          <span className={cn("shrink-0 font-medium", tone)}>
            #{pullRequest.number}
          </span>
          <span className="min-w-0 truncate text-muted-foreground">
            {pullRequest.repoFullName}
          </span>
          <span className="hidden min-w-0 truncate text-muted-foreground/70 sm:block">
            {pullRequest.headRef}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            <HealthSummary health={health} />
            <span className="hidden sm:inline-flex">
              <DiffStat
                additions={pullRequest.diffStats.additions}
                deletions={pullRequest.diffStats.deletions}
              />
            </span>
            <PrStateBadge state={state} />
          </span>
        </HoverCardTrigger>
        <HoverCardPopup
          side="top"
          align="start"
          sideOffset={8}
          className="w-auto shadow-2xl"
        >
          <PullRequestHoverCard
            pullRequest={pullRequest}
            health={health}
            healthUnavailable={healthUnavailable}
          />
        </HoverCardPopup>
      </HoverCard>
      {actionable && onFix && (
        <Button
          variant="destructive"
          aria-label={`Fix PR #${pullRequest.number} issues`}
          disabled={fixDisabled || fixing}
          onClick={() => void handleFix()}
          className="h-auto gap-1.5 rounded-lg border-destructive/30 px-3"
        >
          <Wrench className="size-3.5" />
          {fixing ? "Starting…" : fixFailed ? "Retry" : "Fix"}
        </Button>
      )}
    </div>
  )
}

export function ThreadPullRequests({
  pullRequests,
  health,
  healthUnavailable = false,
  onFix,
  fixDisabled = false,
}: {
  pullRequests: Array<AgentPullRequest>
  health?: Array<AgentPullRequestHealth>
  healthUnavailable?: boolean
  onFix?: (pullRequest: AgentPullRequest) => Promise<void> | void
  fixDisabled?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  if (pullRequests.length === 0) return null

  const healthByPullRequest = new Map(
    (health ?? []).map((item) => [
      healthKey(item.repoFullName ?? "", item.number ?? -1),
      item,
    ])
  )
  const visiblePullRequests = expanded ? pullRequests : pullRequests.slice(-1)
  const hiddenCount = pullRequests.length - 1

  return (
    <div data-testid="thread-pull-requests" className="space-y-1.5 pb-2">
      {visiblePullRequests.map((pullRequest) => (
        <PullRequestLink
          key={healthKey(pullRequest.repoFullName, pullRequest.number)}
          pullRequest={pullRequest}
          health={healthByPullRequest.get(
            healthKey(pullRequest.repoFullName, pullRequest.number)
          )}
          healthUnavailable={healthUnavailable}
          onFix={onFix}
          fixDisabled={fixDisabled}
        />
      ))}
      {hiddenCount > 0 && (
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
          className="font-normal text-muted-foreground"
        >
          {expanded ? (
            <ChevronUp className="size-3.5" />
          ) : (
            <ChevronDown className="size-3.5" />
          )}
          {expanded ? "Show less" : `Show ${hiddenCount} more`}
        </Button>
      )}
    </div>
  )
}
