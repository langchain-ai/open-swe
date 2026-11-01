import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { Link } from "@tanstack/react-router"
import { createContext, useContext, useMemo } from "react"
import type { ComponentProps, ReactNode } from "react"

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

/** A GitHub PR link shown as the Open SWE review page plus a GitHub link. */
function PullRequestLinkChip({
  href,
  repoFullName,
  number,
  children,
  className: _className,
  target: _target,
  rel: _rel,
  ...props
}: ComponentProps<"a"> & { repoFullName: string; number: number }) {
  const [owner, repo] = repoFullName.split("/")
  return (
    <span className="pr-link-chip">
      <Link
        {...props}
        to="/agents/reviews/$owner/$repo/$number"
        params={{ owner: owner!, repo: repo!, number: String(number) }}
      >
        {children === href ? `${repo}#${number}` : children}
      </Link>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        aria-label={`Open ${repoFullName}#${number} on GitHub`}
        title="Open on GitHub"
      >
        <GithubLogoIcon size={13} weight="regular" aria-hidden />
      </a>
    </span>
  )
}

export function PreviewablePullRequestLink({
  href,
  children,
  ...props
}: ComponentProps<"a">) {
  const previews = useContext(PullRequestPreviewContext)
  const parsed = parseGitHubPullRequestUrl(href)
  const pullRequest =
    (href && previews?.pullRequestUrls.get(href)) ||
    (parsed &&
      previews?.pullRequests.get(
        pullRequestKey(parsed.repoFullName, parsed.number)
      ))
  const ref = parsed ?? (pullRequest && previews ? pullRequest : null)
  const link = ref ? (
    <PullRequestLinkChip
      href={href}
      repoFullName={ref.repoFullName}
      number={ref.number}
      {...props}
    >
      {children}
    </PullRequestLinkChip>
  ) : (
    <a href={href} {...props}>
      {children}
    </a>
  )

  if (!pullRequest || !previews) return link

  return (
    <HoverCard openDelay={250} closeDelay={100}>
      <HoverCardTrigger asChild>{link}</HoverCardTrigger>
      <HoverCardContent
        side="top"
        align="start"
        sideOffset={8}
        className={PULL_REQUEST_HOVER_CARD_CLASS}
      >
        <PullRequestHoverCard
          pullRequest={pullRequest}
          health={previews.health.get(
            pullRequestKey(pullRequest.repoFullName, pullRequest.number)
          )}
          healthUnavailable={previews.healthUnavailable}
        />
      </HoverCardContent>
    </HoverCard>
  )
}
