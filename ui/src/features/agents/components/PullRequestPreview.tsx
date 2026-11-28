import { SlackLinks } from "@/lib/slack-links"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import { useQuery } from "@tanstack/react-query"
import { createContext, useContext, useMemo } from "react"
import type { ComponentProps, ReactElement, ReactNode } from "react"

import {
  PullRequestCard,
  PullRequestChip,
} from "@/features/agents/components/PullRequestMention"
import type { PullRequestMentionProps } from "@/features/agents/components/PullRequestMention"
import { SidebarRowHoverCard } from "@/features/agents/components/SidebarRowHoverCard"
import {
  PULL_REQUEST_HOVER_CARD_CLASS,
  PullRequestHoverCard,
} from "@/features/agents/components/ThreadPullRequests"
import type {
  AgentPullRequest,
  AgentPullRequestHealth,
} from "@/features/agents/lib/types"
import { pullRequestPreviewQuery } from "@/features/reviews/lib/cache"

interface PullRequestPreviewContextValue {
  pullRequests: Map<string, AgentPullRequest>
  pullRequestUrls: Map<string, AgentPullRequest>
  health: Map<string, AgentPullRequestHealth>
  healthUnavailable: boolean
}

const PullRequestPreviewContext =
  createContext<PullRequestPreviewContextValue | null>(null)

function pullRequestKey(repoFullName: string, number: number): string {
  return `${repoFullName.toLowerCase()}#${number}`
}

export function parseGitHubPullRequestUrl(
  href: string | undefined
): { repoFullName: string; number: number } | null {
  if (!href) return null
  try {
    const url = new URL(href)
    if (url.hostname !== "github.com" && url.hostname !== "www.github.com") {
      return null
    }
    const match = /^\/([^/]+)\/([^/]+)\/pull\/(\d+)\/?$/.exec(url.pathname)
    if (!match) return null
    return {
      repoFullName: `${match[1]}/${match[2]}`,
      number: Number(match[3]),
    }
  } catch {
    return null
  }
}

export function PullRequestPreviewProvider({
  pullRequests,
  health,
  healthUnavailable,
  children,
}: {
  pullRequests: Array<AgentPullRequest>
  health?: Array<AgentPullRequestHealth>
  healthUnavailable: boolean
  children: ReactNode
}) {
  const value = useMemo(
    () => ({
      pullRequests: new Map(
        pullRequests.map((pullRequest) => [
          pullRequestKey(pullRequest.repoFullName, pullRequest.number),
          pullRequest,
        ])
      ),
      pullRequestUrls: new Map(
        pullRequests.map((pullRequest) => [pullRequest.url, pullRequest])
      ),
      health: new Map(
        (health ?? []).map((item) => [
          pullRequestKey(item.repoFullName ?? "", item.number ?? -1),
          item,
        ])
      ),
      healthUnavailable,
    }),
    [health, healthUnavailable, pullRequests]
  )

  return (
    <PullRequestPreviewContext value={value}>
      {children}
    </PullRequestPreviewContext>
  )
}

/** What the chat knows about a pull request link, or `null` when it is not one. */
function usePullRequestMention(
  href: string | undefined
): PullRequestMentionProps | null {
  const previews = useContext(PullRequestPreviewContext)
  const parsed = parseGitHubPullRequestUrl(href)
  const pullRequest =
    (href && previews?.pullRequestUrls.get(href)) ||
    (parsed &&
      previews?.pullRequests.get(
        pullRequestKey(parsed.repoFullName, parsed.number)
      )) ||
    undefined
  const ref = parsed ?? pullRequest
  if (!href || !ref) return null
  return {
    href,
    repoFullName: ref.repoFullName,
    number: ref.number,
    pullRequest,
    health:
      pullRequest &&
      previews?.health.get(
        pullRequestKey(pullRequest.repoFullName, pullRequest.number)
      ),
  }
}

export function PreviewablePullRequestLink({
  href,
  children,
  ...props
}: ComponentProps<"a">) {
  const mention = usePullRequestMention(href)
  if (!mention)
    return (
      <a href={href ? SlackLinks.normalize(href) : href} {...props}>
        {children}
      </a>
    )
  return (
    <PullRequestPreviewLink {...props} mention={mention}>
      {children}
    </PullRequestPreviewLink>
  )
}

function PullRequestPreviewLink({
  mention,
  children,
  ...props
}: ComponentProps<"a"> & { mention: PullRequestMentionProps }) {
  const previews = useContext(PullRequestPreviewContext)
  return (
    <PullRequestPreviewHoverCard
      mention={mention}
      healthUnavailable={previews?.healthUnavailable ?? false}
      showHealth={mention.pullRequest !== undefined}
    >
      <PullRequestChip {...props} {...mention}>
        {children}
      </PullRequestChip>
    </PullRequestPreviewHoverCard>
  )
}

const failingCheckConclusions = new Set([
  "failure",
  "error",
  "timed_out",
  "action_required",
  "startup_failure",
])

interface PullRequestPreviewContentProps {
  mention: PullRequestMentionProps
  createdAt?: string | null
  healthUnavailable?: boolean
  mergeConflictState?: AgentPullRequestHealth["mergeConflictState"]
  showHealth?: boolean
}

export function PullRequestPreviewHoverCard({
  sidebar = false,
  children,
  ...props
}: PullRequestPreviewContentProps & {
  sidebar?: boolean
  children: ReactElement
}) {
  const card = <PullRequestPreviewContent {...props} />
  if (sidebar) {
    return (
      <SidebarRowHoverCard
        trigger={children}
        card={card}
        className={PULL_REQUEST_HOVER_CARD_CLASS}
        openDelay={250}
      />
    )
  }

  return (
    <HoverCard openDelay={250} closeDelay={100}>
      <HoverCardTrigger asChild>{children}</HoverCardTrigger>
      <HoverCardContent
        side="top"
        align="start"
        sideOffset={8}
        className={PULL_REQUEST_HOVER_CARD_CLASS}
      >
        {card}
      </HoverCardContent>
    </HoverCard>
  )
}

function PullRequestPreviewContent({
  mention,
  createdAt = null,
  healthUnavailable = false,
  mergeConflictState = null,
  showHealth = true,
}: PullRequestPreviewContentProps) {
  const preview = useQuery({
    ...pullRequestPreviewQuery({
      repo: mention.repoFullName,
      number: mention.number,
    }),
    enabled: !mention.pullRequest,
  })
  const data = preview.data
  const state =
    data?.state === "merged" || data?.state === "closed" ? data.state : "open"
  const pullRequest: AgentPullRequest | undefined =
    mention.pullRequest ??
    (data
      ? {
          repoFullName: mention.repoFullName,
          number: mention.number,
          url: mention.href,
          title: data.title,
          state: state === "open" && data.draft ? "draft" : state,
          headRef: data.head_ref,
          baseRef: data.base_ref,
          author: data.author,
          authorAvatarUrl: data.author_avatar_url,
          createdAt,
          diffStats: {
            files: data.changed_files,
            additions: data.additions,
            deletions: data.deletions,
          },
        }
      : undefined)

  const health: AgentPullRequestHealth | undefined =
    mention.health ??
    (showHealth && data
      ? {
          repoFullName: mention.repoFullName,
          number: mention.number,
          url: mention.href,
          statusAvailable: true,
          state,
          isDraft: data.draft,
          mergeConflictState,
          checksAvailable: data.checks != null,
          failingChecks:
            data.checks?.filter(
              (check) =>
                check.name !== "Robin Approves" &&
                check.status === "completed" &&
                failingCheckConclusions.has(check.conclusion ?? "")
            ) ?? [],
          pendingCheckCount: null,
          inconclusiveCheckCount: null,
          commentsAvailable: data.unresolved != null,
          unresolvedReviewThreadCount: data.unresolved?.length ?? null,
          unresolvedReviewThreads: data.unresolved ?? [],
        }
      : undefined)
  return pullRequest ? (
    <PullRequestHoverCard
      pullRequest={pullRequest}
      health={health}
      healthUnavailable={healthUnavailable}
      showHealth={showHealth}
    />
  ) : (
    <p role="status" className="p-space-2 text-xs text-secondary">
      {preview.isError
        ? "Pull request preview unavailable."
        : "Loading pull request preview…"}
    </p>
  )
}

/** A pull request link on its own line as a card; anything else renders `fallback`. */
export function PreviewablePullRequestCard({
  href,
  label,
  fallback,
}: {
  href: string
  label: string
  fallback: ReactNode
}) {
  const mention = usePullRequestMention(href)
  return mention ? <PullRequestCard {...mention} label={label} /> : fallback
}
