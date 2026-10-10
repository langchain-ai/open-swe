import {
  CaretDownIcon,
  CaretUpIcon,
  WarningRegularIcon,
  WrenchRegularIcon,
} from "@langchain/macaw-components/icons"
import { Avatar } from "@langchain/macaw-components/Avatar"
import { Button } from "@langchain/macaw-components/Button"
import { DropdownMenuItem } from "@langchain/macaw-components/DropdownMenu"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"
import { useState } from "react"

import type {
  AgentPullRequest,
  AgentPullRequestHealth,
  ThreadFixScope,
} from "@/features/agents/lib/types"
import { SplitButton } from "@/components/SplitButton"
import { cn } from "@/lib/utils"

/** Surface for {@link PullRequestHoverCard}; the card sets its own width. */
export const PULL_REQUEST_HOVER_CARD_CLASS = "w-auto rounded-lg p-space-3"

export type ThreadFix = (
  pullRequest: AgentPullRequest,
  scope: ThreadFixScope
) => Promise<void> | void

const PR_STATE_STYLES: Record<AgentPullRequest["state"], string> = {
  draft: "bg-surface-level-2 text-secondary",
  open: "bg-success text-success-secondary",
  merged: "bg-purple text-purple",
  closed: "bg-error text-error-secondary",
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
  if (state === "merged") return "text-brand-primary"
  if (state === "closed") return "text-error-secondary"
  if (hasActionableIssues(pullRequest, health)) return "text-error-secondary"
  if (state === "draft") return "text-secondary"
  if (
    !health?.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable ||
    health.mergeConflictState !== "mergeable"
  ) {
    return "text-secondary"
  }
  if (
    (health.pendingCheckCount ?? 0) > 0 ||
    (health.inconclusiveCheckCount ?? 0) > 0
  ) {
    return "text-warning-secondary"
  }
  return "text-success-secondary"
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
  icon: IconComponent
  iconClassName: string
  count?: number
  label: string
}) {
  return (
    <span className="flex items-center gap-space-1 whitespace-nowrap">
      <Icon size={14} weight="regular" className={iconClassName} />
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
          icon={XCircleIcon}
          iconClassName="text-icon-error"
          count={failingCount}
          label={plural(failingCount, "check")}
        />
      )}
      {commentCount > 0 && (
        <HealthItem
          icon={ChatCircleIcon}
          iconClassName="text-icon-warning"
          count={commentCount}
          label={plural(commentCount, "comment")}
        />
      )}
      {health.mergeConflictState === "conflicting" && (
        <HealthItem
          icon={WarningRegularIcon}
          iconClassName="text-icon-error"
          label="Conflict"
        />
      )}
      {pendingCount > 0 && (
        <HealthItem
          icon={ClockIcon}
          iconClassName="text-icon-warning"
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
      <p className="border-t border-subtle pt-space-3 text-xs text-secondary">
        GitHub health is unavailable. This PR is not marked clean.
      </p>
    )
  }
  if (!health) {
    return <p className="text-xs text-secondary">Loading PR health…</p>
  }
  const healthUnavailable =
    !health.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable
  return (
    <div className="space-y-space-3 border-t border-subtle pt-space-3">
      {health.mergeConflictState === "conflicting" && (
        <div className="flex items-start gap-space-2 text-sm text-error-secondary">
          <WarningRegularIcon size={16} className="mt-0.5 shrink-0" />
          <span>This branch has merge conflicts.</span>
        </div>
      )}
      {health.failingChecks.length > 0 && (
        <div className="space-y-space-2" data-testid="pr-failing-checks">
          <p className="text-xs font-medium text-primary">
            Failing checks ({health.failingChecks.length})
          </p>
          {health.failingChecks.map((check, index) => {
            const label = check.name || "Unnamed check"
            const content = (
              <>
                <WarningRegularIcon
                  size={14}
                  className="shrink-0 text-icon-error"
                />
                <span className="min-w-0 flex-1 truncate">{label}</span>
                {check.conclusion && (
                  <span className="shrink-0 text-secondary">
                    {check.conclusion.replaceAll("_", " ")}
                  </span>
                )}
              </>
            )
            return (
              <div
                key={`${label}-${index}`}
                className="flex items-center gap-space-2 px-space-1 py-0.5 text-xs text-primary"
              >
                {content}
              </div>
            )
          })}
        </div>
      )}
      {(health.unresolvedReviewThreadCount ?? 0) > 0 && (
        <div className="space-y-space-2" data-testid="pr-unresolved-comments">
          <p className="text-xs font-medium text-primary">
            Unresolved comments ({health.unresolvedReviewThreadCount})
          </p>
          {health.unresolvedReviewThreads.map((thread, index) => {
            const location = thread.path
              ? `${thread.path}${thread.line ? `:${thread.line}` : ""}`
              : "Pull request"
            const content = (
              <>
                <div className="flex items-center gap-space-2 text-xxs text-secondary">
                  <ChatCircleIcon
                    size={12}
                    weight="regular"
                    className="shrink-0"
                  />
                  <span>{thread.author ?? "Unknown author"}</span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{location}</span>
                </div>
                <p className="line-clamp-2 text-xs text-primary">
                  {thread.body || "No comment text"}
                </p>
              </>
            )
            return (
              <div
                key={`${location}-${index}`}
                className="space-y-space-1 px-space-1 py-0.5"
              >
                {content}
              </div>
            )
          })}
        </div>
      )}
      {healthUnavailable && (
        <p className="text-xs text-secondary">
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
  const state = pullRequestState(pullRequest, health)

  return (
    <div
      data-testid={`pr-hover-card-${pullRequest.repoFullName}-${pullRequest.number}`}
      className="w-96 max-w-[calc(100vw-2rem)] space-y-space-3 p-space-1"
    >
      <div className="flex items-center gap-space-2 text-sm">
        <span
          className={cn(
            "rounded-full px-space-2 py-space-1 text-xs font-medium capitalize",
            PR_STATE_STYLES[state]
          )}
        >
          {state}
        </span>
        <span className="min-w-0 truncate text-secondary">
          {pullRequest.repoFullName} #{pullRequest.number}
        </span>
        {age && (
          <time
            dateTime={pullRequest.createdAt ?? undefined}
            suppressHydrationWarning
            className="ml-auto shrink-0 text-secondary"
          >
            {age}
          </time>
        )}
      </div>
      <p className="text-base leading-snug font-medium text-primary">
        {pullRequest.title}
      </p>
      <div className="flex min-w-0 items-center gap-space-2 text-xs text-secondary">
        <span className="truncate">{pullRequest.baseRef}</span>
        <span aria-hidden="true">←</span>
        <span className="truncate">{pullRequest.headRef}</span>
      </div>
      <div className="flex items-center gap-space-2 text-sm text-secondary">
        <Avatar
          size="sm"
          label={pullRequest.author ?? "Unknown author"}
          imageUrl={pullRequest.authorAvatarUrl ?? undefined}
        />
        <span className="min-w-0 truncate">
          {pullRequest.author ?? "Unknown author"}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-space-2">
          <span className="text-success-secondary">
            +{pullRequest.diffStats.additions}
          </span>
          <span className="text-error-secondary">
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
    <div className="@container flex min-w-0 items-center gap-space-1 rounded-xl border border-default bg-surface-level-1 p-space-1 text-xs text-secondary shadow-sm">
      <HoverCard openDelay={250} closeDelay={100}>
        <HoverCardTrigger asChild>
          <a
            href={pullRequest.url}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${pullRequest.repoFullName} pull request #${pullRequest.number}`}
            data-testid={`pr-summary-${pullRequest.repoFullName}-${pullRequest.number}`}
            data-pr-tone={tone}
            className="flex min-w-0 flex-1 items-center gap-space-2 overflow-hidden rounded-lg px-space-2 py-space-1 transition-colors hover:bg-surface-level-1-hover"
          >
            <GitPullRequestIcon
              size={14}
              weight="regular"
              className={cn("shrink-0", tone)}
            />
            <span className="shrink-0 font-medium text-primary">
              #{pullRequest.number}
            </span>
            <span className="min-w-0 flex-1 truncate">{pullRequest.title}</span>
            <span className="flex shrink-0 items-center gap-space-2 @xl:gap-space-3">
              <HealthSummary health={health} />
              <span className="hidden gap-space-1 @2xl:flex">
                <span className="text-success-secondary">
                  +{pullRequest.diffStats.additions}
                </span>
                <span className="text-error-secondary">
                  -{pullRequest.diffStats.deletions}
                </span>
              </span>
              <span
                className={cn(
                  "rounded-full px-space-2 py-0.5 font-medium capitalize",
                  PR_STATE_STYLES[state]
                )}
              >
                {state}
              </span>
            </span>
          </a>
        </HoverCardTrigger>
        <HoverCardContent
          side="top"
          align="start"
          sideOffset={8}
          className={PULL_REQUEST_HOVER_CARD_CLASS}
        >
          <PullRequestHoverCard
            pullRequest={pullRequest}
            health={health}
            healthUnavailable={healthUnavailable}
          />
        </HoverCardContent>
      </HoverCard>
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
  const [failed, setFailed] = useState<ThreadFixScope | null>(null)
  const handleFix = async (scope: ThreadFixScope) => {
    setFailed(null)
    setFixing(true)
    try {
      await onFix(pullRequest, scope)
    } catch (error) {
      console.error("Could not start a pull request fix", { scope, error })
      setFailed(scope)
    } finally {
      setFixing(false)
    }
  }
  // The first problem is the one to fix first; the caret offers the others.
  const primary = failed ?? scopes[0]!
  return (
    <SplitButton
      size="md"
      variant="plain"
      icon={WrenchRegularIcon}
      disabled={disabled || fixing}
      onClick={() => void handleFix(primary)}
      menuLabel={`Fix PR #${pullRequest.number}`}
      menuSide="top"
      menu={
        scopes.length > 1 &&
        scopes
          .filter((scope) => scope !== primary)
          .map((scope) => (
            <DropdownMenuItem
              key={scope}
              aria-label={`${FIX_LABELS[scope]} on PR #${pullRequest.number}`}
              onSelect={() => void handleFix(scope)}
            >
              {FIX_LABELS[scope]}
            </DropdownMenuItem>
          ))
      }
    >
      {fixing
        ? "Starting…"
        : failed
          ? `Retry: ${FIX_LABELS[failed]}`
          : FIX_LABELS[primary]}
    </SplitButton>
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
    <div
      data-testid="thread-pull-requests"
      className="space-y-space-2 pb-space-2"
    >
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
          color="secondary"
          variant="plain"
          aria-expanded={expanded}
          leftDecorator={expanded ? CaretUpIcon : CaretDownIcon}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Show less" : `Show ${hiddenCount} more`}
        </Button>
      )}
    </div>
  )
}
