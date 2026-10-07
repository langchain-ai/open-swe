import { useState } from "react"
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Clock,
  GitPullRequest,
  MessageCircle,
  Wrench,
  XCircle,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

import type {
  AgentPullRequest,
  AgentPullRequestHealth,
  ThreadFixScope,
} from "@/features/agents/lib/types"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export type ThreadFix = (
  pullRequest: AgentPullRequest,
  scope: ThreadFixScope
) => Promise<void> | void

const PR_STATE_STYLES: Record<AgentPullRequest["state"], string> = {
  draft: "bg-muted text-muted-foreground",
  open: "bg-success/15 text-success-foreground",
  merged: "bg-merged/15 text-merged-foreground",
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

function HealthItem({
  icon: Icon,
  iconClassName,
  count,
  label,
}: {
  icon: LucideIcon
  iconClassName: string
  count?: number
  label: string
}) {
  return (
    <span className="flex items-center gap-1 whitespace-nowrap">
      <Icon className={cn("size-3.5", iconClassName)} />
      {count}
      <span className="hidden @xl:inline">
        {count === undefined ? label : ` ${label}`}
      </span>
    </span>
  )
}

const plural = (count: number, noun: string) =>
  `${noun}${count === 1 ? "" : "s"}`

function HealthSummary({
  health,
}: {
  health: AgentPullRequestHealth | undefined
}) {
  if (!health) return null
  const failingCount = health.failingChecks.length
  const commentCount = health.unresolvedReviewThreadCount ?? 0
  const pendingCount = health.pendingCheckCount ?? 0
  return (
    <>
      {failingCount > 0 && (
        <HealthItem
          icon={XCircle}
          iconClassName="text-destructive"
          count={failingCount}
          label={plural(failingCount, "check")}
        />
      )}
      {commentCount > 0 && (
        <HealthItem
          icon={MessageCircle}
          iconClassName="text-warning-foreground"
          count={commentCount}
          label={plural(commentCount, "comment")}
        />
      )}
      {health.mergeConflictState === "conflicting" && (
        <HealthItem
          icon={AlertTriangle}
          iconClassName="text-destructive"
          label="Conflict"
        />
      )}
      {pendingCount > 0 && (
        <HealthItem
          icon={Clock}
          iconClassName="text-warning-foreground"
          count={pendingCount}
          label="pending"
        />
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
        <span
          className={cn(
            "rounded-full px-2.5 py-1 text-xs font-medium capitalize",
            PR_STATE_STYLES[state]
          )}
        >
          {state}
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
  onFix?: ThreadFix
  fixDisabled: boolean
}) {
  const state = pullRequestState(pullRequest, health)
  const tone = pullRequestTone(pullRequest, health)
  const scopes = onFix ? fixScopes(pullRequest, health) : []

  return (
    <div className="@container flex min-w-0 items-center gap-1 rounded-xl border border-foreground/20 bg-card p-1 text-xs text-muted-foreground shadow-sm dark:border-foreground/[0.06] dark:bg-[#222] dark:shadow-none">
      <Tooltip>
        <TooltipTrigger
          render={
            <a
              href={pullRequest.url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${pullRequest.repoFullName} pull request #${pullRequest.number}`}
              data-testid={`pr-summary-${pullRequest.repoFullName}-${pullRequest.number}`}
              data-pr-tone={tone}
              className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-lg px-2 py-1 transition-colors hover:bg-muted dark:hover:bg-muted/50"
            />
          }
        >
          <GitPullRequest className={cn("size-3.5 shrink-0", tone)} />
          <span className="shrink-0 font-medium text-foreground">
            #{pullRequest.number}
          </span>
          <span className="min-w-0 flex-1 truncate">{pullRequest.title}</span>
          <span className="flex shrink-0 items-center gap-2 @xl:gap-3">
            <HealthSummary health={health} />
            <span className="hidden gap-1 @2xl:flex">
              <span className="text-success-foreground">
                +{pullRequest.diffStats.additions}
              </span>
              <span className="text-destructive">
                -{pullRequest.diffStats.deletions}
              </span>
            </span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 font-medium capitalize",
                PR_STATE_STYLES[state]
              )}
            >
              {state}
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
          <PullRequestHoverCard
            pullRequest={pullRequest}
            health={health}
            healthUnavailable={healthUnavailable}
          />
        </TooltipPopup>
      </Tooltip>
      {onFix && scopes.length > 0 && (
        <FixMenu
          pullRequest={pullRequest}
          scopes={scopes}
          onFix={onFix}
          disabled={fixDisabled}
        />
      )}
    </div>
  )
}

const FIX_LABELS: Record<ThreadFixScope, string> = {
  conflicts: "Fix conflicts",
  checks: "Fix checks",
  comments: "Address comments",
}

/** One item per problem, so a fix never quietly takes on the others. */
function fixScopes(
  pullRequest: AgentPullRequest,
  health: AgentPullRequestHealth | undefined
): Array<ThreadFixScope> {
  if (!health || !hasActionableIssues(pullRequest, health)) return []
  return [
    ...(health.mergeConflictState === "conflicting"
      ? (["conflicts"] as const)
      : []),
    ...(health.failingChecks.length > 0 ? (["checks"] as const) : []),
    ...((health.unresolvedReviewThreadCount ?? 0) > 0
      ? (["comments"] as const)
      : []),
  ]
}

function FixMenu({
  pullRequest,
  scopes,
  onFix,
  disabled,
}: {
  pullRequest: AgentPullRequest
  scopes: Array<ThreadFixScope>
  onFix: ThreadFix
  disabled: boolean
}) {
  const [fixing, setFixing] = useState(false)
  const [failed, setFailed] = useState(false)
  const handleFix = async (scope: ThreadFixScope) => {
    setFailed(false)
    setFixing(true)
    try {
      await onFix(pullRequest, scope)
    } catch (error) {
      console.error("Could not start a pull request fix", { scope, error })
      setFailed(true)
    } finally {
      setFixing(false)
    }
  }
  return (
    <Menu>
      <MenuTrigger
        disabled={disabled || fixing}
        render={
          <Button
            variant="ghost"
            aria-label={`Fix PR #${pullRequest.number}`}
            className="text-foreground"
          />
        }
      >
        <Wrench />
        {fixing ? "Starting…" : failed ? "Retry fix" : "Fix"}
        <ChevronDown />
      </MenuTrigger>
      <MenuPopup align="end" side="top" sideOffset={6}>
        {scopes.map((scope) => (
          <MenuItem
            key={scope}
            aria-label={`${FIX_LABELS[scope]} on PR #${pullRequest.number}`}
            onClick={() => void handleFix(scope)}
          >
            {FIX_LABELS[scope]}
          </MenuItem>
        ))}
      </MenuPopup>
    </Menu>
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
  onFix?: ThreadFix
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
