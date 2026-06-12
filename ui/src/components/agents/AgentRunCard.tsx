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
import type { ComponentType, SVGProps } from "react"

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

interface AgentRunCardProps {
  thread: AgentThread
}

export function AgentRunCard({ thread }: AgentRunCardProps) {
  const stats = thread.diffStats
  const hasPr = Boolean(thread.pr)
  const source = thread.source ? SOURCE_META[thread.source] : null
  const SourceIcon = source?.icon

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
        <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-[var(--ui-text-dim)]">
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
          <span className="min-w-0 truncate" title={thread.model}>
            {thread.model}
          </span>
          {thread.repo && (
            <>
              <span className="shrink-0">·</span>
              <span className="min-w-0 truncate" title={thread.repo}>
                {thread.repo}
              </span>
            </>
          )}
          <span className="shrink-0">·</span>
          <span className="shrink-0 whitespace-nowrap">
            {formatRelativeTime(thread.updatedAt)}
          </span>
        </div>
      </div>

      <CheckCircleIcon
        className={cn(
          "size-5 shrink-0",
          thread.status === "finished"
            ? "text-[var(--ui-text-dim)] opacity-100"
            : "opacity-0"
        )}
        weight="regular"
      />
    </Link>
  )
}
