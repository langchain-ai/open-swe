import { useState } from "react"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/gtm-platform-design-system/ui/hover-card"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import type {
  AgentPullRequest,
  AgentPullRequestHealth,
  ThreadFixScope,
} from "@/features/agents/lib/types"
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  GitPullRequest,
  MessageCircle,
  Wrench,
} from "@/components/glyphs"

/** The transcript's PR previews open on the same beat. */
const PREVIEW_DELAY_MS = 250

export type ThreadFix = (
  pullRequest: AgentPullRequest,
  scope: ThreadFixScope
) => Promise<void> | void

type BadgeTone = "positive" | "attention" | "risk" | "info" | "neutral"

/* Merged is the app's own state pair; the other three are the system's tones. */
const PR_STATE_BADGE: Record<
  AgentPullRequest["state"],
  { tone: BadgeTone; className?: string }
> = {
  draft: { tone: "neutral" },
  open: { tone: "positive" },
  merged: {
    tone: "neutral",
    className: "border-merged/20 bg-merged-bg text-merged",
  },
  closed: { tone: "risk" },
}

function PullRequestStateBadge({ state }: { state: AgentPullRequest["state"] }) {
  const badge = PR_STATE_BADGE[state]
  return (
    <Badge
      tier="quiet"
      tone={badge.tone}
      className={cn("capitalize", badge.className)}
    >
      {state}
    </Badge>
  )
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

/** The ink that says how the PR is doing; also published as `data-pr-tone`. */
function pullRequestTone(
  pullRequest: AgentPullRequest,
  health: AgentPullRequestHealth | undefined
): string {
  const state = pullRequestState(pullRequest, health)
  if (state === "merged") return "text-merged"
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

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

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
        <Badge tier="quiet" tone="risk">
          {plural(failingCount, "check")}
        </Badge>
      )}
      {commentCount > 0 && (
        <Badge tier="quiet" tone="attention">
          {plural(commentCount, "comment")}
        </Badge>
      )}
      {health.mergeConflictState === "conflicting" && (
        <Badge tier="quiet" tone="risk">
          Conflict
        </Badge>
      )}
      {pendingCount > 0 && (
        <Badge tier="quiet" tone="attention">
          {pendingCount} pending
        </Badge>
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
      <Box
        render={<p />}
        className="border-t border-line pt-3 text-meta text-ink-subtle"
      >
        GitHub health is unavailable. This PR is not marked clean.
      </Box>
    )
  }
  if (!health) {
    return (
      <Box render={<p />} className="text-meta text-ink-subtle">
        Loading PR health…
      </Box>
    )
  }
  const healthUnavailable =
    !health.statusAvailable ||
    !health.checksAvailable ||
    !health.commentsAvailable
  return (
    <Stack gap="md" className="border-t border-line pt-3">
      {health.mergeConflictState === "conflicting" && (
        <Inline gap="sm" align="start" ink="risk" className="text-label">
          <Icon icon={AlertTriangle} />
          This branch has merge conflicts.
        </Inline>
      )}
      {health.failingChecks.length > 0 && (
        <Stack gap="xs" data-testid="pr-failing-checks">
          <Box render={<p />} className="text-label font-medium text-ink">
            Failing checks ({health.failingChecks.length})
          </Box>
          {health.failingChecks.map((check, index) => {
            const label = check.name || "Unnamed check"
            return (
              <Inline
                key={`${label}-${index}`}
                gap="sm"
                align="center"
                className="text-label text-ink"
              >
                <Icon icon={AlertTriangle} size="sm" className="text-risk" />
                <Box render={<span />} className="min-w-0 flex-1 truncate">
                  {label}
                </Box>
                {check.conclusion && (
                  <Box render={<span />} className="shrink-0 text-ink-subtle">
                    {check.conclusion.replaceAll("_", " ")}
                  </Box>
                )}
              </Inline>
            )
          })}
        </Stack>
      )}
      {(health.unresolvedReviewThreadCount ?? 0) > 0 && (
        <Stack gap="sm" data-testid="pr-unresolved-comments">
          <Box render={<p />} className="text-label font-medium text-ink">
            Unresolved comments ({health.unresolvedReviewThreadCount})
          </Box>
          {health.unresolvedReviewThreads.map((thread, index) => {
            const location = thread.path
              ? `${thread.path}${thread.line ? `:${thread.line}` : ""}`
              : "Pull request"
            return (
              <Stack key={`${location}-${index}`} gap="xs">
                <Inline
                  gap="xs"
                  align="center"
                  ink="ink-subtle"
                  className="min-w-0 text-meta"
                >
                  <Icon icon={MessageCircle} size="sm" />
                  <Box render={<span />}>
                    {thread.author ?? "Unknown author"}
                  </Box>
                  <Box render={<span />} aria-hidden="true">
                    ·
                  </Box>
                  <Box render={<span />} className="truncate">
                    {location}
                  </Box>
                </Inline>
                <Box render={<p />} className="line-clamp-2 text-label text-ink">
                  {thread.body || "No comment text"}
                </Box>
              </Stack>
            )
          })}
        </Stack>
      )}
      {healthUnavailable && (
        <Box render={<p />} className="text-meta text-ink-subtle">
          Some GitHub health details are unavailable. This PR is not marked
          clean.
        </Box>
      )}
    </Stack>
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
    <Stack
      gap="md"
      data-testid={`pr-hover-card-${pullRequest.repoFullName}-${pullRequest.number}`}
      className="min-w-0"
    >
      <Inline gap="sm" align="center" className="text-label">
        <PullRequestStateBadge state={state} />
        <Box render={<span />} className="min-w-0 truncate text-ink-subtle">
          {pullRequest.repoFullName} #{pullRequest.number}
        </Box>
        {age && (
          <Box
            render={
              <time
                dateTime={pullRequest.createdAt ?? undefined}
                suppressHydrationWarning
              />
            }
            className="ml-auto shrink-0 text-meta text-ink-subtle"
          >
            {age}
          </Box>
        )}
      </Inline>
      <Box render={<p />} className="text-title font-medium text-ink">
        {pullRequest.title}
      </Box>
      <Inline
        gap="sm"
        align="center"
        ink="ink-subtle"
        className="min-w-0 font-mono text-meta"
      >
        <Box render={<span />} className="truncate">
          {pullRequest.baseRef}
        </Box>
        <Box render={<span />} aria-hidden="true">
          ←
        </Box>
        <Box render={<span />} className="truncate">
          {pullRequest.headRef}
        </Box>
      </Inline>
      <Inline gap="sm" align="center" ink="ink-subtle" className="text-label">
        <Avatar
          name={pullRequest.author ?? "Unknown author"}
          src={pullRequest.authorAvatarUrl ?? undefined}
          size="chat"
        />
        <Box render={<span />} className="min-w-0 truncate">
          {pullRequest.author ?? "Unknown author"}
        </Box>
        <Inline
          gap="sm"
          align="center"
          className="ml-auto shrink-0 font-mono tabular-nums"
        >
          <Box render={<span />} className="text-positive">
            +{pullRequest.diffStats.additions}
          </Box>
          <Box render={<span />} className="text-risk">
            -{pullRequest.diffStats.deletions}
          </Box>
          <Box render={<span />} className="font-sans">
            {plural(pullRequest.diffStats.files, "file")}
          </Box>
        </Inline>
      </Inline>
      <HealthDetails health={health} unavailable={healthUnavailable} />
    </Stack>
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
    <Inline gap="sm" align="stretch" className="min-w-0">
      <HoverCard>
        <HoverCardTrigger
          delay={PREVIEW_DELAY_MS}
          href={pullRequest.url}
          target="_blank"
          rel="noreferrer"
          aria-label={`Open ${pullRequest.repoFullName} pull request #${pullRequest.number}`}
          data-testid={`pr-summary-${pullRequest.repoFullName}-${pullRequest.number}`}
          data-pr-tone={tone}
          className="flex h-row-data min-w-0 flex-1 items-center gap-2 rounded-control border border-line bg-panel px-3 text-label transition-colors duration-fast ease-out-quint outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none"
        >
          <Icon icon={GitPullRequest} className={tone} />
          <Box
            render={<span />}
            className={cn("shrink-0 font-medium tabular-nums", tone)}
          >
            #{pullRequest.number}
          </Box>
          {compact ? (
            <Box render={<span />} className="min-w-0 truncate text-ink-subtle">
              {pullRequest.title}
            </Box>
          ) : (
            <>
              <Box
                render={<span />}
                className="min-w-0 truncate text-ink-subtle"
              >
                {pullRequest.repoFullName}
              </Box>
              <Box
                render={<span />}
                className="hidden min-w-0 truncate font-mono text-meta text-ink-subtle sm:block"
              >
                {pullRequest.headRef}
              </Box>
            </>
          )}
          <Inline gap="sm" align="center" className="ml-auto shrink-0">
            <HealthSummary health={health} />
            <Box
              render={<span />}
              className="hidden font-mono text-meta text-positive tabular-nums sm:inline"
            >
              +{pullRequest.diffStats.additions}
            </Box>
            <Box
              render={<span />}
              className="hidden font-mono text-meta text-risk tabular-nums sm:inline"
            >
              -{pullRequest.diffStats.deletions}
            </Box>
            <PullRequestStateBadge state={state} />
          </Inline>
        </HoverCardTrigger>
        <HoverCardContent side="top" align="start" sideOffset={8} className="w-96">
          <PullRequestHoverCard
            pullRequest={pullRequest}
            health={health}
            healthUnavailable={healthUnavailable}
          />
        </HoverCardContent>
      </HoverCard>
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
    </Inline>
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
    <Button
      variant="outline"
      aria-label={`${FIX_LABELS[scope]} on PR #${pullRequest.number}`}
      disabled={disabled || fixing}
      onClick={() => void handleFix()}
      className="h-auto border-risk/40 text-risk hover:bg-risk-bg"
    >
      <Icon icon={Wrench} size="sm" />
      {fixing ? "Starting…" : failed ? "Retry" : FIX_LABELS[scope]}
    </Button>
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
    <Stack gap="sm" data-testid="thread-pull-requests" className="pb-2">
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
        <Box>
          <Button
            variant="ghost"
            size="compact"
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
            className="font-normal text-ink-subtle"
          >
            <Icon icon={expanded ? ChevronUp : ChevronDown} size="sm" />
            {expanded ? "Show less" : `Show ${hiddenCount} more`}
          </Button>
        </Box>
      )}
    </Stack>
  )
}
