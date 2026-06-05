import { Link } from "@tanstack/react-router"
import {
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
      <div className="flex items-center justify-between px-5 pt-6 pb-4 md:px-4 md:pt-5">
        <Link
          to="/my-settings"
          className="flex items-center gap-2 font-heading text-lg font-medium tracking-tight text-[var(--ui-text)] md:text-sm"
        >
          <img src="/logo-mark.png" alt="" className="size-6 md:size-5" />
          open-swe
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <div className="px-3 pb-1 md:px-2">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="flex min-h-11 w-full touch-manipulation items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium text-[var(--ui-text)] transition-colors hover:bg-[var(--ui-sidebar-hover)] md:min-h-0 md:gap-2.5 md:rounded-md md:px-2.5 md:py-1.5 md:text-xs"
        >
          <PlusIcon className="size-5 md:size-4" />
          New Agent
        </Link>
      </div>

      <nav className="flex flex-col gap-1 px-3 pb-4 md:gap-0.5 md:px-2">
        {NAV.map((item) => {
          const Icon = item.icon
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={layout.closeOnMobile}
              className="flex min-h-11 touch-manipulation items-center gap-3 rounded-xl px-4 py-3 text-sm text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)] md:min-h-0 md:gap-2.5 md:rounded-md md:px-2.5 md:py-1.5 md:text-xs"
            >
              <Icon className="size-5 md:size-4" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3 md:px-2 md:pb-2">
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

      <div className="p-3 md:p-2">
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
    <div className="mb-5 md:mb-3">
      <div className="px-4 py-2 text-xs font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase md:px-2 md:py-1 md:text-[10px]">
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
        "group mb-1 flex min-h-14 touch-manipulation items-center gap-3 rounded-xl px-4 py-3 transition-colors md:mb-0.5 md:min-h-0 md:gap-2 md:rounded-lg md:px-2.5 md:py-1.5",
        isActive
          ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
          : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]",
        isDeleting && "opacity-50"
      )}
    >
      <span
        className={cn(
          "size-2.5 shrink-0 rounded-full md:size-2",
          thread.status === "running"
            ? "animate-pulse bg-[var(--ui-accent)]"
            : thread.status === "finished"
              ? "bg-[var(--ui-accent)]"
              : "bg-[var(--ui-border)]"
        )}
      />
      {source && SourceIcon && (
        <SourceIcon
          className="size-[18px] shrink-0 text-[var(--ui-text-dim)] md:size-3.5"
          aria-label={source.label}
        >
          <title>{source.label}</title>
        </SourceIcon>
      )}
      <span className="min-w-0 flex-1 truncate text-base md:text-xs">
        {thread.title}
      </span>
      {badge && (
        <span className="hidden shrink-0 rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 text-[10px] text-[var(--ui-text-dim)] md:block md:group-hover:hidden">
          {badge}
        </span>
      )}
      <button
        type="button"
        aria-label="Delete thread"
        onClick={onDelete}
        disabled={isDeleting}
        className="hidden size-10 shrink-0 items-center justify-center rounded-full text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)] max-md:flex md:size-4 md:rounded md:group-hover:flex"
      >
        <XIcon className="size-4 md:size-3" weight="bold" />
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
