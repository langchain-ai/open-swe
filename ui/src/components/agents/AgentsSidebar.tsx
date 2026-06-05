import { Link } from "@tanstack/react-router"
import {
  CalendarBlankIcon,
  ChartLineUpIcon,
  ChatCircleIcon,
  PlusIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import type { ComponentType, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { AgentSource, AgentThread } from "@/lib/agents/types"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import { groupThreads } from "@/lib/agents/api"
import { useAgentThreads, useDeleteAgentThread } from "@/lib/agents/queries"
import { cn } from "@/lib/utils"

type SourceIcon = ComponentType<SVGProps<SVGSVGElement>>

const SOURCE_META: Record<AgentSource, { icon: SourceIcon; label: string }> = {
  dashboard: { icon: ChatCircleIcon, label: "Started from the dashboard" },
  github: { icon: IoLogoGithub, label: "Triggered from GitHub" },
  slack: { icon: IoLogoSlack, label: "Triggered from Slack" },
  linear: { icon: SiLinear, label: "Triggered from Linear" },
  schedule: { icon: CalendarBlankIcon, label: "Triggered from a schedule" },
}

interface AgentsSidebarProps {
  user: SessionUser
  activeThreadId?: string
}

const NAV = [
  { to: "/my-settings", label: "Dashboard", icon: ChartLineUpIcon },
] as const

export function AgentsSidebar({ user, activeThreadId }: AgentsSidebarProps) {
  const threadsQuery = useAgentThreads()
  const threads = threadsQuery.data ?? []
  const groups = groupThreads(threads)
  const layout = useSidebarLayout()

  return (
    <SidebarFrame
      {...layout}
      className="border-r border-[var(--ui-border)] bg-[var(--ui-sidebar)]"
    >
      <div className="flex items-center justify-between px-4 pt-5 pb-4">
        <Link
          to="/my-settings"
          className="flex items-center gap-2 font-heading text-sm font-medium tracking-tight text-[var(--ui-text)]"
        >
          <img src="/logo-mark.png" alt="" className="size-5" />
          open-swe
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <div className="px-2 pb-1">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-[var(--ui-text)] transition-colors hover:bg-[var(--ui-sidebar-hover)]"
        >
          <PlusIcon className="size-4" />
          New Agent
        </Link>
      </div>

      <nav className="flex flex-col gap-0.5 px-2 pb-4">
        {NAV.map((item) => {
          const Icon = item.icon
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={layout.closeOnMobile}
              className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)]"
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <ThreadGroup
          label="Today"
          threads={groups.today}
          activeThreadId={activeThreadId}
          onNavigate={layout.closeOnMobile}
        />
        <ThreadGroup
          label="Last 30 days"
          threads={groups.last30}
          activeThreadId={activeThreadId}
          onNavigate={layout.closeOnMobile}
        />
        <ThreadGroup
          label="Older"
          threads={groups.older}
          activeThreadId={activeThreadId}
          onNavigate={layout.closeOnMobile}
        />
      </div>

      <div className="p-2">
        <SidebarUserMenu user={user} showSettingsLink />
      </div>
    </SidebarFrame>
  )
}

function ThreadGroup({
  label,
  threads,
  activeThreadId,
  onNavigate,
}: {
  label: string
  threads: Array<AgentThread>
  activeThreadId?: string
  onNavigate?: () => void
}) {
  if (threads.length === 0) return null

  return (
    <div className="mb-3">
      <div className="px-2 py-1 text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
        {label}
      </div>
      {threads.map((thread) => (
        <ThreadRow
          key={thread.id}
          thread={thread}
          isActive={thread.id === activeThreadId}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  )
}

function ThreadRow({
  thread,
  isActive,
  onNavigate,
}: {
  thread: AgentThread
  isActive: boolean
  onNavigate?: () => void
}) {
  const deleteThread = useDeleteAgentThread()
  const badge =
    thread.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null
  const isDeleting =
    deleteThread.isPending && deleteThread.variables === thread.id

  const onDelete = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (isDeleting) return
    if (!window.confirm(`Delete "${thread.title}"? This cannot be undone.`))
      return
    deleteThread.mutate(thread.id)
  }

  const source =
    thread.source && thread.source !== "dashboard"
      ? SOURCE_META[thread.source]
      : null
  const SourceIcon = source?.icon

  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId: thread.id }}
      onClick={onNavigate}
      className={cn(
        "group mb-0.5 flex items-center gap-2 rounded-lg px-2.5 py-1.5 transition-colors",
        isActive
          ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
          : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]",
        isDeleting && "opacity-50"
      )}
    >
      <span
        className={cn(
          "size-2 shrink-0 rounded-full",
          thread.status === "running"
            ? "animate-pulse bg-[var(--ui-accent)]"
            : thread.status === "finished"
              ? "bg-[var(--ui-accent)]"
              : "bg-[var(--ui-border)]"
        )}
      />
      {source && SourceIcon && (
        <SourceIcon
          className="size-3.5 shrink-0 text-[var(--ui-text-dim)]"
          aria-label={source.label}
        >
          <title>{source.label}</title>
        </SourceIcon>
      )}
      <span className="min-w-0 flex-1 truncate text-xs">{thread.title}</span>
      {badge && (
        <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 text-[10px] text-[var(--ui-text-dim)] group-hover:hidden">
          {badge}
        </span>
      )}
      <button
        type="button"
        aria-label="Delete thread"
        onClick={onDelete}
        disabled={isDeleting}
        className="hidden size-4 shrink-0 items-center justify-center rounded text-[var(--ui-text-dim)] group-hover:flex hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
      >
        <XIcon className="size-3" weight="bold" />
      </button>
    </Link>
  )
}

export function AgentsShell({
  user,
  activeThreadId,
  children,
}: {
  user: SessionUser
  activeThreadId?: string
  children: React.ReactNode
}) {
  return (
    <div className="agents-ui flex h-svh overflow-hidden bg-[var(--ui-bg)]">
      <AgentsSidebar user={user} activeThreadId={activeThreadId} />
      <div className="flex min-w-0 flex-1">{children}</div>
    </div>
  )
}
