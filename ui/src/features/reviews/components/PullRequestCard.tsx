import type { ReactNode } from "react"

import type { OpenPullRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import { dateLabel } from "../lib/dateLabel"
import {
  hasUnresolvedConversations,
  isFixable,
  statusLabels,
} from "../lib/status"
import { PullRequestLinks } from "../PullRequestLinks"
import { AddressPullRequestComments } from "./AddressPullRequestComments"
import { ClosePullRequest } from "./ClosePullRequest"
import { Diffstat } from "./Diffstat"
import { FixPullRequest } from "./FixPullRequest"
import { MarkPullRequestReady } from "./MarkPullRequestReady"
import { MergePullRequest } from "./MergePullRequest"
import { PullRequestChecks } from "./PullRequestChecks"
import { StatusPill } from "./StatusPill"
import { UnresolvedConversations } from "./UnresolvedConversations"

export function PullRequestCard({
  pr,
  login,
  selected,
  onSelect,
  review,
  onRemoved,
  onReady,
}: {
  pr: OpenPullRequest
  login: string
  selected: boolean
  onSelect: (include: boolean) => void
  review: ReactNode
  onRemoved: () => void
  onReady: () => void
}) {
  return (
    <li
      className={cn(
        "rounded-lg border bg-card p-4",
        selected ? "border-primary bg-primary/5" : "border-border"
      )}
    >
      <div className="flex gap-3">
        <input
          type="checkbox"
          className="mt-0.5 shrink-0 self-start"
          aria-label={`Select PR #${pr.number} in ${pr.repo}`}
          checked={selected}
          onChange={(event) => onSelect(event.target.checked)}
        />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
              <span>
                {pr.repo}{" "}
                <span className="font-mono tabular-nums">#{pr.number}</span>
              </span>
              {statusLabels(pr).map((status) => (
                <StatusPill key={status} status={status} />
              ))}
            </div>
            <h3 className="mt-1 text-sm font-medium break-words">
              <a
                className="hover:underline"
                href={`https://github.com/${pr.repo}/pull/${pr.number}`}
                target="_blank"
                rel="noreferrer"
              >
                {pr.title}
              </a>
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
            <Diffstat pr={pr} />
            {review}
            <span>
              Updated{" "}
              <time
                dateTime={pr.updatedAt ?? undefined}
                title={pr.updatedAt ?? undefined}
              >
                {dateLabel(pr.updatedAt)}
              </time>
            </span>
            <span>
              Opened{" "}
              <time
                dateTime={pr.createdAt ?? undefined}
                title={pr.createdAt ?? undefined}
              >
                {dateLabel(pr.createdAt)}
              </time>
            </span>
            <UnresolvedConversations pr={pr} />
            {!pr.statusAvailable && !pr.detailsLoading && (
              <span className="text-amber-700 dark:text-amber-400">
                Live PR status unavailable
              </span>
            )}
          </div>
          <PullRequestChecks pr={pr} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
              {isFixable(pr) && <FixPullRequest pr={pr} login={login} />}
              {hasUnresolvedConversations(pr) && (
                <AddressPullRequestComments pr={pr} login={login} />
              )}
              {pr.draft === true && (
                <MarkPullRequestReady pr={pr} onReady={onReady} />
              )}
              <MergePullRequest pr={pr} onMerged={onRemoved} />
              <ClosePullRequest pr={pr} onClosed={onRemoved} />
            </div>
            <PullRequestLinks
              repo={pr.repo}
              number={pr.number}
              title={pr.title}
            />
          </div>
        </div>
      </div>
    </li>
  )
}
