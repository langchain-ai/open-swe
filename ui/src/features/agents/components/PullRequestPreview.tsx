import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import { createContext, useContext, useMemo } from "react"
import type { ComponentProps, ReactNode } from "react"

import {
  PullRequestCard,
  PullRequestChip,
} from "@/features/agents/components/PullRequestMention"
import type { PullRequestMentionProps } from "@/features/agents/components/PullRequestMention"
import {
  PULL_REQUEST_HOVER_CARD_CLASS,
  PullRequestHoverCard,
} from "@/features/agents/components/ThreadPullRequests"
import type {
  AgentPullRequest,
  AgentPullRequestHealth,
} from "@/features/agents/lib/types"

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
  const previews = useContext(PullRequestPreviewContext)
  const mention = usePullRequestMention(href)
  if (!mention)
    return (
      <a href={href} {...props}>
        {children}
      </a>
    )

  const chip = (
    <PullRequestChip {...props} {...mention}>
      {children}
    </PullRequestChip>
  )
  if (!mention.pullRequest || !previews) return chip

  return (
    <HoverCard openDelay={250} closeDelay={100}>
      <HoverCardTrigger asChild>{chip}</HoverCardTrigger>
      <HoverCardContent
        side="top"
        align="start"
        sideOffset={8}
        className={PULL_REQUEST_HOVER_CARD_CLASS}
      >
        <PullRequestHoverCard
          pullRequest={mention.pullRequest}
          health={mention.health}
          healthUnavailable={previews.healthUnavailable}
        />
      </HoverCardContent>
    </HoverCard>
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
