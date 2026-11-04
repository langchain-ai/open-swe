import { IconButton } from "@langchain/macaw-components/IconButton"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { Link } from "@tanstack/react-router"
import type { ComponentProps, ReactNode } from "react"

import {
  PR_STATE_STYLES,
  pullRequestState,
  pullRequestTone,
} from "@/features/agents/components/ThreadPullRequests"
import type {
  AgentPullRequest,
  AgentPullRequestHealth,
} from "@/features/agents/lib/types"
import { cn } from "@/lib/utils"

/** A pull request mentioned in chat: where it lives on GitHub and what the thread knows about it. */
export interface PullRequestMentionProps {
  href: string
  repoFullName: string
  number: number
  pullRequest?: AgentPullRequest
  health?: AgentPullRequestHealth
}

function ReviewLink({
  repoFullName,
  number,
  children,
  ...props
}: Omit<ComponentProps<"a">, "href"> & {
  repoFullName: string
  number: number
  children: ReactNode
}) {
  const [owner, repo] = repoFullName.split("/")
  return (
    <Link
      {...props}
      to="/agents/reviews/$owner/$repo/$number"
      params={{ owner: owner!, repo: repo!, number: String(number) }}
    >
      {children}
    </Link>
  )
}

/** Inline mention: the Open SWE review page, then a GitHub mark. */
export function PullRequestChip({
  href,
  repoFullName,
  number,
  children,
  className: _className,
  target: _target,
  rel: _rel,
  ...props
}: Omit<ComponentProps<"a">, "href"> & PullRequestMentionProps) {
  return (
    <span className="pr-link-chip">
      <ReviewLink {...props} repoFullName={repoFullName} number={number}>
        {children === href
          ? `${repoFullName.split("/")[1]}#${number}`
          : children}
      </ReviewLink>
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

/** A pull request link on its own line: a card that opens the review page, with a GitHub button. */
export function PullRequestCard({
  href,
  repoFullName,
  number,
  pullRequest,
  health,
  label,
}: PullRequestMentionProps & { label?: string }) {
  const state = pullRequest && pullRequestState(pullRequest, health)
  const tone = pullRequest
    ? pullRequestTone(pullRequest, health)
    : "text-secondary"
  const title =
    pullRequest?.title ||
    (label && label !== href
      ? label
      : `${repoFullName.split("/")[1]}#${number}`)

  return (
    <div
      data-testid={`pr-card-${repoFullName}-${number}`}
      className="pr-card my-[0.65rem] flex max-w-xl min-w-0 items-center gap-space-1 rounded-xl border border-default bg-surface-level-1 p-space-1 text-xs text-secondary shadow-sm"
    >
      <ReviewLink
        repoFullName={repoFullName}
        number={number}
        aria-label={`Review ${repoFullName} pull request #${number} in Open SWE`}
        className="flex min-w-0 flex-1 items-center gap-space-3 rounded-lg px-space-2 py-space-1 transition-colors hover:bg-surface-level-1-hover"
      >
        <GitPullRequestIcon
          size={18}
          weight="regular"
          className={cn("shrink-0", tone)}
        />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium text-primary">
            {title}
          </span>
          <span className="flex min-w-0 items-center gap-space-2">
            <span className="truncate">
              {repoFullName} #{number}
            </span>
            {pullRequest && (
              <>
                <span className="text-success-secondary">
                  +{pullRequest.diffStats.additions}
                </span>
                <span className="text-error-secondary">
                  -{pullRequest.diffStats.deletions}
                </span>
              </>
            )}
          </span>
        </span>
        {state && (
          <span
            className={cn(
              "shrink-0 rounded-full px-space-2 py-0.5 font-medium capitalize",
              PR_STATE_STYLES[state]
            )}
          >
            {state}
          </span>
        )}
      </ReviewLink>
      <IconButton
        asChild
        icon={GithubLogoIcon}
        label={`Open ${repoFullName}#${number} on GitHub`}
        size="sm"
        color="secondary"
        variant="plain"
        tooltipProps={{ title: "Open on GitHub" }}
      >
        <a href={href} target="_blank" rel="noreferrer" />
      </IconButton>
    </div>
  )
}
