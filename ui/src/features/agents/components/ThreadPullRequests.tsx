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
  ThreadFixScope,
} from "@/features/agents/lib/types"
import { Avatar, AvatarFallback, AvatarImage } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@langchain/gtm-platform-design-system/ui/tooltip"
import { cn } from "@/lib/utils"

export type ThreadFix = (
  pullRequest: AgentPullRequest,
  scope: ThreadFixScope
) => Promise<void> | void

const PR_STATE_STYLES: Record<AgentPullRequest["state"], string> = {
  draft: "bg-muted text-ink-subtle",
  open: "bg-positive-bg text-positive",
  merged: "bg-merged/15 text-merged",
  closed: "bg-risk-bg text-risk",
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
  if (state === "merged") return "text-info"
  if (state === "closed") return "text-risk"
  if (hasActionableIssues(pullRequest, health)) return "text-risk"
  if (state === "draft") return "text-ink-subtle"
  if (
    !health?.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable ||
    health.mergeConflictState !== "mergeable"
  ) {
    return "text-ink-subtle"
  }
  if (
    (health.pendingCheckCount ?? 0) > 0 ||
    (health.inconclusiveCheckCount ?? 0) > 0
  ) {
    return "text-attention"
  }
  return "text-positive"
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
        <span className="rounded-full bg-risk-bg px-2 py-0.5 font-medium text-risk">
          {failingCount} check{failingCount === 1 ? "" : "s"}
        </span>
      )}
      {commentCount > 0 && (
        <span className="rounded-full bg-attention-bg px-2 py-0.5 font-medium text-attention">
          {commentCount} comment{commentCount === 1 ? "" : "s"}
        </span>
      )}
      {health.mergeConflictState === "conflicting" && (
        <span className="rounded-full bg-risk-bg px-2 py-0.5 font-medium text-risk">
          Conflict
        </span>
      )}
      {(health.pendingCheckCount ?? 0) > 0 && (
        <span className="rounded-full bg-attention-bg px-2 py-0.5 font-medium text-attention">
          {health.pendingCheckCount} pending
        </span>
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
      <p className="border-t border-line/70 pt-3 text-meta text-ink-subtle">
        GitHub health is unavailable. This PR is not marked clean.
      </p>
    )
  }
  if (!health) {
    return <p className="text-meta text-ink-subtle">Loading PR health…</p>
  }
  const healthUnavailable =
    !health.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable
  return (
    <div className="space-y-3 border-t border-line/70 pt-3">
      {health.mergeConflictState === "conflicting" && (
        <div className="flex items-start gap-2 text-body text-risk">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>This branch has merge conflicts.</span>
        </div>
      )}
      {health.failingChecks.length > 0 && (
        <div className="space-y-1.5" data-testid="pr-failing-checks">
          <p className="text-label font-medium text-ink">
            Failing checks ({health.failingChecks.length})
          </p>
          {health.failingChecks.map((check, index) => {
            const label = check.name || "Unnamed check"
            const content = (
              <>
                <AlertTriangle className="size-3.5 shrink-0 text-risk" />
                <span className="min-w-0 flex-1 truncate">{label}</span>
                {check.conclusion && (
                  <span className="shrink-0 text-ink-subtle">
                    {check.conclusion.replaceAll("_", " ")}
                  </span>
                )}
              </>
            )
            return (
              <div
                key={`${label}-${index}`}
                className="flex items-center gap-2 px-1 py-0.5 text-label text-ink"
              >
                {content}
              </div>
            )
          })}
        </div>
      )}
      {(health.unresolvedReviewThreadCount ?? 0) > 0 && (
        <div className="space-y-2" data-testid="pr-unresolved-comments">
          <p className="text-label font-medium text-ink">
            Unresolved comments ({health.unresolvedReviewThreadCount})
          </p>
          {health.unresolvedReviewThreads.map((thread, index) => {
            const location = thread.path
              ? `${thread.path}${thread.line ? `:${thread.line}` : ""}`
              : "Pull request"
            const content = (
              <>
                <div className="flex items-center gap-1.5 text-meta text-ink-subtle">
                  <MessageCircle className="size-3 shrink-0" />
                  <span>{thread.author ?? "Unknown author"}</span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{location}</span>
                </div>
                <p className="line-clamp-2 text-label text-ink">
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
        <p className="text-meta text-ink-subtle">
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
      <div className="flex items-center gap-2 text-body">
        <span
          className={cn(
            "rounded-full px-2.5 py-1 text-label font-medium capitalize",
            PR_STATE_STYLES[state]
          )}
        >
          {state}
        </span>
        <span className="min-w-0 truncate text-ink-subtle">
          {pullRequest.repoFullName} #{pullRequest.number}
        </span>
        {age && (
          <time
            dateTime={pullRequest.createdAt ?? undefined}
            suppressHydrationWarning
            className="ml-auto shrink-0 text-ink-subtle"
          >
            {age}
          </time>
        )}
      </div>
      <p className="text-title leading-snug font-medium text-ink">
        {pullRequest.title}
      </p>
      <div className="flex min-w-0 items-center gap-2 text-meta text-ink-subtle">
        <span className="truncate">{pullRequest.baseRef}</span>
        <span aria-hidden="true">←</span>
        <span className="truncate">{pullRequest.headRef}</span>
      </div>
      <div className="flex items-center gap-2 text-body text-ink-subtle">
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
          <span className="text-positive">
            +{pullRequest.diffStats.additions}
          </span>
          <span className="text-risk">
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
  compact = false,
}: {
  pullRequest: AgentPullRequest
  health: AgentPullRequestHealth | undefined
  healthUnavailable: boolean
  onFix?: ThreadFix
  fixDisabled: boolean
  compact?: boolean
}) {
  const state = pullRequestState(pullRequest, health)
  const tone = pullRequestTone(pullRequest, health)

  return (
    <div className="flex min-w-0 items-stretch gap-1.5">
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
              className="group flex min-w-0 flex-1 items-center gap-2 rounded-compact border border-line/70 bg-panel/80 px-3 py-2 text-label shadow-control transition-colors hover:border-line hover:bg-hover/70"
            />
          }
        >
          <GitPullRequest className={cn("size-4 shrink-0", tone)} />
          <span className={cn("shrink-0 font-medium", tone)}>
            #{pullRequest.number}
          </span>
          {compact ? (
            <span className="min-w-0 truncate text-ink-subtle">
              {pullRequest.title}
            </span>
          ) : (
            <>
              <span className="min-w-0 truncate text-ink-subtle">
                {pullRequest.repoFullName}
              </span>
              <span className="hidden min-w-0 truncate text-ink-subtle/70 sm:block">
                {pullRequest.headRef}
              </span>
            </>
          )}
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            <HealthSummary health={health} />
            <span className="hidden text-positive sm:inline">
              +{pullRequest.diffStats.additions}
            </span>
            <span className="hidden text-risk sm:inline">
              -{pullRequest.diffStats.deletions}
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
        <TooltipContent
          side="top"
          align="start"
          sideOffset={8}
          className="rounded-control p-3 shadow-overlay"
        >
          <PullRequestHoverCard
            pullRequest={pullRequest}
            health={health}
            healthUnavailable={healthUnavailable}
          />
        </TooltipContent>
      </Tooltip>
      {onFix &&
        !compact &&
        fixScopes(pullRequest, health).map((scope) => (
          <FixButton
            key={scope}
            pullRequest={pullRequest}
            scope={scope}
            onFix={onFix}
            disabled={fixDisabled}
          />
        ))}
    </div>
  )
}

const FIX_LABELS: Record<ThreadFixScope, string> = {
  conflicts: "Fix conflicts",
  checks: "Fix checks",
  comments: "Address comments",
}

/** One button per problem, so a fix never quietly takes on the others. */
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

function FixButton({
  pullRequest,
  scope,
  onFix,
  disabled,
}: {
  pullRequest: AgentPullRequest
  scope: ThreadFixScope
  onFix: ThreadFix
  disabled: boolean
}) {
  const [fixing, setFixing] = useState(false)
  const [failed, setFailed] = useState(false)
  const handleFix = async () => {
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
    <button
      type="button"
      aria-label={`${FIX_LABELS[scope]} on PR #${pullRequest.number}`}
      disabled={disabled || fixing}
      onClick={() => void handleFix()}
      className="flex shrink-0 items-center gap-1.5 rounded-compact border border-risk/30 bg-risk-bg px-3 text-label font-medium text-risk transition-colors hover:bg-risk-bg disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Wrench className="size-3.5" />
      {fixing ? "Starting…" : failed ? "Retry" : FIX_LABELS[scope]}
    </button>
  )
}

export function ThreadPullRequests({
  pullRequests,
  health,
  healthUnavailable = false,
  onFix,
  fixDisabled = false,
  compact = false,
}: {
  pullRequests: Array<AgentPullRequest>
  health?: Array<AgentPullRequestHealth>
  healthUnavailable?: boolean
  onFix?: ThreadFix
  fixDisabled?: boolean
  /** One-line row above the composer: title instead of repo and branch. */
  compact?: boolean
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
          compact={compact}
        />
      ))}
      {hiddenCount > 0 && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
          className="flex items-center gap-1 rounded-badge px-2 py-1 text-meta text-ink-subtle transition-colors hover:bg-hover hover:text-ink"
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
