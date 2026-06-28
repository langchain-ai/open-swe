import { Link } from "@tanstack/react-router"
import {
  CalendarBlankIcon,
  ChatCircleIcon,
  CheckCircleIcon,
  GitBranchIcon,
  GitPullRequestIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import type { ComponentType, ReactNode, SVGProps } from "react"

import type { AgentSource, AgentThread } from "@/lib/agents/types"
import { cn, formatRelativeTime } from "@/lib/utils"

type SourceIcon = ComponentType<SVGProps<SVGSVGElement>>

const SOURCE_META: Record<AgentSource, { icon: SourceIcon; label: string }> = {
  dashboard: { icon: ChatCircleIcon, label: "Dashboard" },
  github: { icon: IoLogoGithub, label: "GitHub" },
  slack: { icon: IoLogoSlack, label: "Slack" },
  linear: { icon: SiLinear, label: "Linear" },
  schedule: { icon: CalendarBlankIcon, label: "Schedule" },
}

function statusTone(status: string | null | undefined): string {
  const normalized = status?.toLowerCase()
  if (!normalized) return ""
  if (["blocked", "error", "failed", "failure"].includes(normalized)) {
    return "border-[var(--ui-danger)]/30 text-[var(--ui-danger)]"
  }
  if (["passed", "ready", "success", "succeeded", "merged"].includes(normalized)) {
    return "border-[var(--ui-success)]/30 text-[var(--ui-success)]"
  }
  return ""
}

function shortSha(sha: string): string {
  return sha.length > 8 ? sha.slice(0, 8) : sha
}

function countLabel(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`
}

function DeliveryBadge({
  children,
  tone,
  title,
}: {
  children: ReactNode
  tone?: string
  title?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel-2)] px-1.5 py-0.5",
        tone
      )}
      title={title}
    >
      {children}
    </span>
  )
}

interface AgentRunCardProps {
  thread: AgentThread
  endAdornment?: ReactNode
}

export function AgentRunCard({ thread, endAdornment }: AgentRunCardProps) {
  const stats = thread.diffStats
  const hasPr = Boolean(thread.pr)
  const source = thread.source ? SOURCE_META[thread.source] : null
  const SourceIcon = source?.icon
  const delivery = thread.delivery
  const deliveryThreads = delivery
    ? [
        ["Worker", delivery.workerThreadId],
        ["Review", delivery.reviewerThreadId],
        ["QA", delivery.qaThreadId],
        ["Merge", delivery.mergeWorkerThreadId],
      ].filter((entry): entry is [string, string] => Boolean(entry[1]))
    : []

  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      className="group flex items-center gap-4 rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3 transition-colors hover:border-[var(--ui-accent)]/30 hover:bg-[var(--ui-panel)]"
    >
      <div className="flex size-[72px] shrink-0 flex-col items-center justify-center rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel-2)] text-center">
        {stats ? (
          <>
            <div className="text-[11px] font-medium text-[var(--ui-text-muted)]">
              {stats.files} {stats.files === 1 ? "file" : "files"}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs font-medium">
              <span className="text-[var(--ui-success)]">
                +{stats.additions}
              </span>
              <span className="text-[var(--ui-danger)]">
                -{stats.deletions}
              </span>
            </div>
          </>
        ) : (
          <GitBranchIcon className="size-5 text-[var(--ui-text-dim)]" />
        )}
        <div className="mt-1.5 flex items-center gap-1 text-[10px] text-[var(--ui-text-dim)]">
          {hasPr ? (
            <>
              <GitPullRequestIcon className="size-3" />
              <span className="capitalize">{thread.pr?.state ?? "open"}</span>
            </>
          ) : (
            <>
              <GitBranchIcon className="size-3" />
              Branch
            </>
          )}
        </div>
      </div>

      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-[var(--ui-text)]">
          {thread.title}
        </div>
        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--ui-text-dim)]">
          {source && SourceIcon && (
            <>
              <span
                className="flex shrink-0 items-center gap-1"
                title={source.label}
              >
                <SourceIcon className="size-3.5" aria-label={source.label} />
                {source.label}
              </span>
              <span className="shrink-0">·</span>
            </>
          )}
          <span className="shrink-0 capitalize">{thread.status}</span>
          {thread.resolved && (
            <>
              <span className="shrink-0">·</span>
              <span className="shrink-0">resolved</span>
            </>
          )}
          <span className="shrink-0">·</span>
          <span className="max-w-36 truncate" title={thread.model}>
            {thread.model}
          </span>
          {thread.repo && (
            <>
              <span className="shrink-0">·</span>
              <span className="max-w-36 truncate" title={thread.repo}>
                {thread.repo}
              </span>
            </>
          )}
          <span className="shrink-0">·</span>
          <span className="shrink-0 whitespace-nowrap">
            {formatRelativeTime(thread.updatedAt)}
          </span>
        </div>
        {delivery && (
          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-1.5 text-[11px] leading-5 text-[var(--ui-text-dim)]">
            <DeliveryBadge tone={statusTone(delivery.queueStatus)}>
              Queue {delivery.queueStatus ?? "unknown"}
            </DeliveryBadge>
            {delivery.gateRollup && (
              <DeliveryBadge tone={statusTone(delivery.gateRollup.status)}>
                Gates {delivery.gateRollup.status}
                {delivery.gateRollup.total > 0 &&
                  ` ${delivery.gateRollup.passed}/${delivery.gateRollup.total}`}
              </DeliveryBadge>
            )}
            {delivery.mergeStatus && (
              <DeliveryBadge tone={statusTone(delivery.mergeStatus)}>
                Merge {delivery.mergeStatus}
              </DeliveryBadge>
            )}
            {delivery.previewCount > 0 && (
              <DeliveryBadge>
                {countLabel(delivery.previewCount, "preview", "previews")}
              </DeliveryBadge>
            )}
            {delivery.artifactCount > 0 && (
              <DeliveryBadge>
                {countLabel(delivery.artifactCount, "artifact", "artifacts")}
              </DeliveryBadge>
            )}
            {delivery.reviewedSha && (
              <DeliveryBadge title={delivery.reviewedSha}>
                Reviewed {shortSha(delivery.reviewedSha)}
              </DeliveryBadge>
            )}
            {deliveryThreads.map(([label, threadId]) => (
              <DeliveryBadge key={label} title={threadId}>
                {label}
              </DeliveryBadge>
            ))}
          </div>
        )}
        {delivery?.blockerReason && (
          <div
            className="mt-1 truncate text-[11px] text-[var(--ui-danger)]"
            title={delivery.blockerReason}
          >
            Blocked: {delivery.blockerReason}
          </div>
        )}
      </div>

      {endAdornment}

      {!endAdornment && (
        <CheckCircleIcon
          className={cn(
            "size-5 shrink-0",
            thread.status === "finished"
              ? "text-[var(--ui-text-dim)] opacity-100"
              : "opacity-0"
          )}
          weight="regular"
        />
      )}
    </Link>
  )
}
