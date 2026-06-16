import { ContextMenu } from "@base-ui/react/context-menu"
import { Dialog } from "@base-ui/react/dialog"
import { Link } from "@tanstack/react-router"
import {
  ArrowCounterClockwiseIcon,
  CalendarBlankIcon,
  CaretDownIcon,
  CaretRightIcon,
  ChartLineUpIcon,
  ChatCircleIcon,
  CheckCircleIcon,
  CircleNotchIcon,
  GitMergeIcon,
  GitPullRequestIcon,
  LightningIcon,
  PlusIcon,
  TrashIcon,
  TreeStructureIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { useState } from "react"
import type { ComponentType, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { AgentSource, AgentThread } from "@/lib/agents/types"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  ReviewSidebarPanel,
  ReviewSidebarProvider,
  useReviewSidebarData,
} from "@/components/agents/ReviewSidebar"
import { Button } from "@/components/ui/button"
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import { groupThreads } from "@/lib/agents/api"
import {
  useDeleteAgentThread,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarThreads,
} from "@/lib/agents/queries"
import { cn } from "@/lib/utils"

const RESOLVED_SIDEBAR_LIMIT = 20

type SourceIcon = ComponentType<SVGProps<SVGSVGElement>>

const SOURCE_META: Record<AgentSource, { icon: SourceIcon; label: string }> = {
  dashboard: { icon: ChatCircleIcon, label: "Started from the dashboard" },
  github: { icon: IoLogoGithub, label: "Triggered from GitHub" },
  slack: { icon: IoLogoSlack, label: "Triggered from Slack" },
  linear: { icon: SiLinear, label: "Triggered from Linear" },
  schedule: { icon: CalendarBlankIcon, label: "Triggered from a schedule" },
}

type PrState = NonNullable<AgentThread["pr"]>["state"]

const PR_STATE_META: Record<
  PrState,
  { icon: SourceIcon; label: string; className: string }
> = {
  draft: {
    icon: GitPullRequestIcon,
    label: "Draft pull request",
    className: "text-[var(--ui-text-dim)]",
  },
  open: {
    icon: GitPullRequestIcon,
    label: "Open pull request",
    className: "text-[var(--ui-success)]",
  },
  merged: {
    icon: GitMergeIcon,
    label: "Merged pull request",
    className: "text-[var(--ui-accent)]",
  },
  closed: {
    icon: GitPullRequestIcon,
    label: "Closed pull request",
    className: "text-[var(--ui-danger)]",
  },
}

interface AgentsSidebarProps {
  user: SessionUser
  activeThreadId?: string
}

const NAV = [
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/my-settings", label: "Dashboard", icon: ChartLineUpIcon },
  { to: "/agents/reviews", label: "Reviews", icon: GitPullRequestIcon },
] as const

export function AgentsSidebar({ user, activeThreadId }: AgentsSidebarProps) {
  const { active, resolved } = useSidebarThreads(RESOLVED_SIDEBAR_LIMIT)
  const activeThreads = active.data?.items ?? []
  const resolvedThreads = resolved.data?.items ?? []
  const resolvedTotal = resolved.data?.total ?? resolvedThreads.length
  useSeedAgentThreadDetails(
    [...activeThreads, ...resolvedThreads],
    activeThreadId
  )
  const groups = groupThreads(activeThreads)
  const layout = useSidebarLayout()
  const reviewSidebar = useReviewSidebarData()

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
              activeProps={{
                className:
                  "bg-[var(--ui-sidebar-hover)] !text-[var(--ui-text)] font-medium",
              }}
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {reviewSidebar ? (
        <ReviewSidebarPanel data={reviewSidebar} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          <ThreadGroup
            label="Today"
            threads={groups.today}
            activeThreadId={activeThreadId}
            onNavigate={layout.closeOnMobile}
          />
          <ThreadGroup
            label="Last 7 days"
            threads={groups.last7}
            activeThreadId={activeThreadId}
            onNavigate={layout.closeOnMobile}
            defaultCollapsed
          />
          <ThreadGroup
            label="Last 30 days"
            threads={groups.last30}
            activeThreadId={activeThreadId}
            onNavigate={layout.closeOnMobile}
            defaultCollapsed
          />
          <ThreadGroup
            label="Older"
            threads={groups.older}
            activeThreadId={activeThreadId}
            onNavigate={layout.closeOnMobile}
          />
          <ResolvedThreadGroup
            threads={resolvedThreads}
            total={resolvedTotal}
            activeThreadId={activeThreadId}
            onNavigate={layout.closeOnMobile}
          />
        </div>
      )}

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
  defaultCollapsed = false,
}: {
  label: string
  threads: Array<AgentThread>
  activeThreadId?: string
  onNavigate?: () => void
  defaultCollapsed?: boolean
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  if (threads.length === 0) return null

  const ToggleIcon = collapsed ? CaretRightIcon : CaretDownIcon

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase transition-colors hover:text-[var(--ui-text-muted)]"
        aria-expanded={!collapsed}
      >
        <ToggleIcon className="size-3" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <span>{threads.length}</span>
      </button>
      {!collapsed &&
        threads.map((thread) => (
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

function ResolvedThreadGroup({
  threads,
  total,
  activeThreadId,
  onNavigate,
}: {
  threads: Array<AgentThread>
  total: number
  activeThreadId?: string
  onNavigate?: () => void
}) {
  const [collapsed, setCollapsed] = useState(true)
  if (threads.length === 0) return null

  const ToggleIcon = collapsed ? CaretRightIcon : CaretDownIcon
  const visible = threads.slice(0, RESOLVED_SIDEBAR_LIMIT)
  const hasMore = total > visible.length

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase transition-colors hover:text-[var(--ui-text-muted)]"
        aria-expanded={!collapsed}
      >
        <ToggleIcon className="size-3" />
        <span className="min-w-0 flex-1 truncate">Resolved</span>
        <span>{total}</span>
      </button>
      {!collapsed && (
        <>
          {visible.map((thread) => (
            <ThreadRow
              key={thread.id}
              thread={thread}
              isActive={thread.id === activeThreadId}
              onNavigate={onNavigate}
            />
          ))}
          {hasMore && (
            <Link
              to="/agents/threads"
              search={{ resolved: true, page: 1 }}
              onClick={onNavigate}
              className="mt-0.5 flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)]"
            >
              Show all
            </Link>
          )}
        </>
      )}
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
  const resolveThread = useResolveAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
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
    setDeleteOpen(true)
  }

  const onConfirmDelete = () => {
    if (isDeleting) return
    deleteThread.mutate(thread.id, {
      onSuccess: () => setDeleteOpen(false),
    })
  }

  const isResolved = thread.resolved === true
  const onToggleResolved = (e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (resolveThread.isPending) return
    resolveThread.mutate({ threadId: thread.id, resolved: !isResolved })
  }

  const source =
    thread.source && thread.source !== "dashboard"
      ? SOURCE_META[thread.source]
      : null
  const SourceIcon = source?.icon
  const prMeta = thread.pr ? PR_STATE_META[thread.pr.state] : null
  const PrIcon = prMeta?.icon
  const showFinishedIndicator = thread.status === "finished" && !thread.viewed

  const openTrace = () => {
    if (!thread.traceUrl) return
    window.open(thread.traceUrl, "_blank", "noopener,noreferrer")
  }

  return (
    <>
      <ContextMenu.Root>
        <ContextMenu.Trigger
          render={
            <Link
              to="/agents/$threadId"
              params={{ threadId: thread.id }}
              onClick={onNavigate}
              className={cn(
                "group mb-0.5 flex h-8 items-center gap-2 rounded-lg px-2.5 transition-colors",
                isActive
                  ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
                  : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]",
                isDeleting && "opacity-50"
              )}
            />
          }
        >
          {thread.status === "running" ? (
            <CircleNotchIcon
              className="size-3 shrink-0 animate-spin text-[var(--ui-accent)]"
              aria-label="Thread running"
            />
          ) : (
            <span
              className={cn(
                "size-2 shrink-0 rounded-full",
                showFinishedIndicator
                  ? "bg-[var(--ui-accent)]"
                  : "bg-[var(--ui-border)]"
              )}
              aria-label={
                showFinishedIndicator ? "Thread finished" : "Thread viewed"
              }
            />
          )}
          {source && SourceIcon && (
            <SourceIcon
              className="size-3.5 shrink-0 text-[var(--ui-text-dim)]"
              aria-label={source.label}
            >
              <title>{source.label}</title>
            </SourceIcon>
          )}
          <span className="min-w-0 flex-1 truncate text-xs">
            {thread.title}
          </span>
          {prMeta && PrIcon && (
            <PrIcon
              className={cn(
                "size-3.5 shrink-0 group-hover:hidden",
                prMeta.className
              )}
              aria-label={prMeta.label}
            >
              <title>{prMeta.label}</title>
            </PrIcon>
          )}
          {badge && (
            <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 text-[10px] text-[var(--ui-text-dim)] group-hover:hidden">
              {badge}
            </span>
          )}
          <button
            type="button"
            aria-label={isResolved ? "Unresolve thread" : "Resolve thread"}
            title={isResolved ? "Unresolve thread" : "Resolve thread"}
            onClick={onToggleResolved}
            disabled={resolveThread.isPending}
            className="hidden size-4 shrink-0 items-center justify-center rounded text-[var(--ui-text-dim)] group-hover:flex hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
          >
            {isResolved ? (
              <ArrowCounterClockwiseIcon className="size-3" weight="bold" />
            ) : (
              <CheckCircleIcon className="size-3" weight="bold" />
            )}
          </button>
          <button
            type="button"
            aria-label="Delete thread"
            onClick={onDelete}
            disabled={isDeleting}
            className="hidden size-4 shrink-0 items-center justify-center rounded text-[var(--ui-text-dim)] group-hover:flex hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
          >
            <XIcon className="size-3" weight="bold" />
          </button>
        </ContextMenu.Trigger>
        <ContextMenu.Portal>
          <ContextMenu.Positioner className="z-50 outline-none">
            <ContextMenu.Popup className="min-w-[10rem] overflow-hidden rounded-md border border-[var(--ui-border)] bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
              <ContextMenu.Item
                disabled={!thread.traceUrl}
                onClick={openTrace}
                className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-[var(--ui-sidebar-hover)] data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                <TreeStructureIcon className="size-3.5" />
                Open trace
              </ContextMenu.Item>
              <ContextMenu.Item
                onClick={() => onToggleResolved()}
                disabled={resolveThread.isPending}
                className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-[var(--ui-sidebar-hover)] data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                {isResolved ? (
                  <ArrowCounterClockwiseIcon className="size-3.5" />
                ) : (
                  <CheckCircleIcon className="size-3.5" />
                )}
                {isResolved ? "Unresolve thread" : "Resolve thread"}
              </ContextMenu.Item>
              <ContextMenu.Item
                onClick={onDelete}
                disabled={isDeleting}
                className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-[var(--ui-danger)] outline-none select-none data-highlighted:bg-[var(--ui-sidebar-hover)] data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                <TrashIcon className="size-3.5" />
                Delete thread
              </ContextMenu.Item>
            </ContextMenu.Popup>
          </ContextMenu.Positioner>
        </ContextMenu.Portal>
      </ContextMenu.Root>
      <Dialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <div className="flex flex-col gap-4">
              <Dialog.Title className="text-base font-semibold">
                Delete thread
              </Dialog.Title>
              <Dialog.Description className="text-sm text-muted-foreground">
                Delete "{thread.title}"? This cannot be undone.
              </Dialog.Description>
              <div className="mt-2 flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setDeleteOpen(false)}
                  disabled={isDeleting}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={onConfirmDelete}
                  disabled={isDeleting}
                >
                  {isDeleting ? "Deleting..." : "Delete"}
                </Button>
              </div>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </>
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
    <ReviewSidebarProvider>
      <div className="agents-ui flex h-svh overflow-hidden bg-[var(--ui-bg)]">
        <AgentsSidebar user={user} activeThreadId={activeThreadId} />
        <div className="flex min-w-0 flex-1">{children}</div>
      </div>
    </ReviewSidebarProvider>
  )
}
