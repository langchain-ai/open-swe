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

import type { AgentSource, AgentThread } from "@/lib/agentTypes"
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
      className="group flex items-center gap-4 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-primary/30 hover:bg-card"
    >
      <div className="flex size-[72px] shrink-0 flex-col items-center justify-center rounded-lg border border-border bg-accent text-center">
        {stats ? (
          <>
            <div className="text-[11px] font-medium text-muted-foreground">
              {stats.files} {stats.files === 1 ? "file" : "files"}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs font-medium">
              <span className="text-success-foreground">
                +{stats.additions}
              </span>
              <span className="text-destructive">-{stats.deletions}</span>
            </div>
          </>
        ) : (
          <GitBranchIcon className="size-5 text-muted-foreground/70" />
        )}
        <div className="mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground/70">
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
        <div className="truncate text-sm font-medium text-foreground">
          {thread.title}
        </div>
        <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground/70">
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
            ? "text-muted-foreground/70 opacity-100"
            : "opacity-0"
        )}
        weight="regular"
      />
    </Link>
  )
}
