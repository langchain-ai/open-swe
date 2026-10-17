import { Badge } from "@langchain/macaw-components/Badge"
import { Text } from "@langchain/macaw-components/Text"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import type { ReactNode } from "react"

import type { PullRequestSnapshot } from "@/features/agents/lib/api"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import { safeModelLabel } from "@/lib/modelLabel"
import { cn } from "@/lib/utils"

export function SidebarThreadCardContent({
  item,
  live,
  conversation,
  age,
  source,
  indicators,
  actions,
}: {
  item: SidebarThreadItem
  live?: PullRequestSnapshot
  conversation: boolean
  age: string
  source: ReactNode
  indicators: ReactNode
  actions: ReactNode
}) {
  const thread = item.thread
  const prState = live?.state ?? item.pr?.state
  const waiting = Boolean(thread.attentionReason)
  const status =
    item.status === "running"
      ? "Running"
      : item.status === "error"
        ? "Run failed"
        : waiting
          ? "Waiting on you"
          : live?.checks === "failing"
            ? "Checks failing"
            : item.status === "interrupted"
              ? "Interrupted"
              : prState === "merged"
                ? "Merged"
                : prState === "closed"
                  ? "Closed"
                  : prState === "draft"
                    ? "Draft PR"
                    : item.reviewPage
                      ? "Review ready"
                      : item.status === "finished"
                        ? "Finished"
                        : "Idle"
  const statusClass =
    item.status === "error" || live?.checks === "failing"
      ? "text-status-red"
      : waiting || item.status === "interrupted"
        ? "text-status-orange"
        : item.status === "running"
          ? "text-brand-primary"
          : prState === "merged"
            ? "text-purple"
            : "text-secondary"
  const stats = thread.diffStats
  const hasChanges =
    stats && (stats.files || stats.additions || stats.deletions)

  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 flex-col",
        conversation ? "gap-space-2" : "gap-space-3"
      )}
    >
      {!conversation && (
        <div className="flex min-w-0 items-center justify-between gap-space-2 text-tertiary">
          <span className="flex min-w-0 items-center gap-space-2">
            <FolderIcon size={14} weight="regular" className="shrink-0" />
            <Text as="span" variant="xs" className="truncate">
              {item.repoLabel || "No repository"}
            </Text>
          </span>
          <Text as="span" variant="xs" className="shrink-0 tabular-nums">
            {age}
          </Text>
        </div>
      )}
      <Text
        as="span"
        variant={conversation ? "sm" : "md"}
        weight="medium"
        className="line-clamp-2 break-words text-primary"
      >
        {item.title}
      </Text>
      <div className="flex min-w-0 items-center justify-between gap-space-2">
        <span
          className={cn(
            "flex min-w-0 flex-wrap items-center gap-space-2 text-xs break-words",
            statusClass
          )}
        >
          {conversation && source}
          <span>{conversation ? "Slack conversation" : status}</span>
          {conversation && <span className="text-tertiary">· {status}</span>}
        </span>
        <span className="flex shrink-0 items-center gap-space-2">
          {indicators}
          {actions}
          {conversation && (
            <Text as="span" variant="xs" className="text-tertiary tabular-nums">
              {age}
            </Text>
          )}
        </span>
      </div>
      {!conversation && (
        <>
          {thread.attentionReason && (
            <Text
              as="span"
              variant="xs"
              color="secondary"
              className="line-clamp-2 break-words"
            >
              {thread.attentionReason}
            </Text>
          )}
          {thread.branch && (
            <span
              className="flex min-w-0 items-center gap-space-2 text-tertiary"
              title={thread.branch}
            >
              <GitBranchIcon size={14} weight="regular" className="shrink-0" />
              <span className="truncate font-mono text-xxs">
                {thread.branch}
              </span>
            </span>
          )}
          {(item.pr || hasChanges || item.taskWorkers?.length) && (
            <span className="flex flex-wrap items-center gap-space-2 text-xs text-secondary">
              {item.pr && (
                <Badge
                  size="sm"
                  color={prState === "open" ? "success" : "secondary"}
                  rounded="sm"
                >
                  {`#${item.pr.number} · ${prState ?? item.pr.state}`}
                </Badge>
              )}
              {hasChanges ? (
                <>
                  <span>
                    {stats.files} {stats.files === 1 ? "file" : "files"}
                  </span>
                  <span className="font-mono tabular-nums">
                    <span className="text-success-secondary">
                      +{stats.additions}
                    </span>{" "}
                    <span className="text-error-secondary">
                      −{stats.deletions}
                    </span>
                  </span>
                </>
              ) : null}
              {Boolean(item.taskWorkers?.length) && (
                <span>{item.taskWorkers!.length} workers</span>
              )}
            </span>
          )}
          <span className="flex min-w-0 flex-wrap items-center justify-between gap-space-2 border-t border-subtle pt-space-2 text-xxs text-tertiary">
            <span className="flex min-w-0 items-center gap-space-2">
              {source}
              {thread.ownerLogin && (
                <span className="truncate">{thread.ownerLogin}</span>
              )}
            </span>
            <span className="min-w-0 truncate" title={item.model}>
              {safeModelLabel(item.model)}
              {thread.costUsd != null && ` · $${thread.costUsd.toFixed(2)}`}
            </span>
          </span>
        </>
      )}
    </div>
  )
}
