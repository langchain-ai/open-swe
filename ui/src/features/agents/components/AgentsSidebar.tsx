import { Menu } from "@base-ui/react/menu"
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
  CopyIcon,
  DotsThreeVerticalIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  GitMergeIcon,
  GitPullRequestIcon,
  LightningIcon,
  PlusIcon,
  SparkleIcon,
  TrashIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { useState } from "react"
import type { ComponentType, ReactNode, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { DesktopAcpSessionSummary, DesktopProject } from "@/desktop"
import type { AgentSource, AgentThread } from "@/features/agents/lib/types"
import type { SidebarLayout } from "@/components/sidebar-layout"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { SidebarFilterMenu } from "@/features/agents/components/SidebarFilterMenu"
import { Button } from "@/components/ui/button"
import {
  SidebarCollapseButton,
  SidebarFrame,
  SidebarLayoutProvider,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import {
  availableFacets,
  filterThreads,
  groupThreadsByMode,
  hasActiveFilters,
} from "@/features/agents/lib/sidebarFilter"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  useDeleteAgentThread,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarThreads,
} from "@/features/agents/lib/queries"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import { useDesktopAcpSessions } from "@/features/agents/lib/desktopAcp"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
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
    className: "text-muted-foreground/70",
  },
  open: {
    icon: GitPullRequestIcon,
    label: "Open pull request",
    className: "text-success-foreground",
  },
  merged: {
    icon: GitMergeIcon,
    label: "Merged pull request",
    className: "text-primary",
  },
  closed: {
    icon: GitPullRequestIcon,
    label: "Closed pull request",
    className: "text-destructive",
  },
}

interface AgentsSidebarProps {
  user: SessionUser
  activeThreadId?: string
  activeLocalSessionId?: string
  layout: SidebarLayout
}

const NAV = [
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/my-settings", label: "Dashboard", icon: ChartLineUpIcon },
  { to: "/agents/reviews", label: "Reviews", icon: GitPullRequestIcon },
] as const

export function AgentsSidebar({
  user,
  activeThreadId,
  activeLocalSessionId,
  layout,
}: AgentsSidebarProps) {
  const {
    prefs,
    setGroup,
    setCompact,
    toggleSection,
    setFilters,
    resetFilters,
  } = useSidebarPrefs()
  const sidebar = useSidebarThreads(RESOLVED_SIDEBAR_LIMIT, activeThreadId)
  const localSessions = useDesktopAcpSessions()
  const {
    projects: localProjects,
    addProject: addLocalProject,
    removeProject: removeLocalProject,
  } = useDesktopProjects()
  const localGroups = groupLocalProjects(localProjects, localSessions)
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const activeThreads = sidebar.data?.active.items ?? []
  const resolvedThreads = sidebar.data?.resolved.items ?? []
  const resolvedHasMore = sidebar.data?.resolved.hasMore ?? false
  const visibleThreads = [...activeThreads, ...resolvedThreads]
  useSeedAgentThreadDetails(visibleThreads, activeThreadId)
  useRunCompletionNotifier(visibleThreads, activeThreadId)

  const facets = availableFacets(visibleThreads)
  const filteredActive = filterThreads(activeThreads, prefs.filters)
  const filteredResolved = filterThreads(resolvedThreads, prefs.filters)
  const sections = groupThreadsByMode(filteredActive, prefs.group)
  const showResolved = prefs.filters.includeResolved
  const isEmpty =
    localGroups.length === 0 &&
    sections.length === 0 &&
    (!showResolved || filteredResolved.length === 0) &&
    hasActiveFilters(prefs.filters)
  const localSessionCount = localGroups.reduce(
    (total, group) => total + group.sessions.length,
    0
  )
  const cloudCollapsed = isDesktop && prefs.collapsed.cloud

  return (
    <SidebarFrame {...layout} className="border-r border-border bg-sidebar">
      <div className="flex items-center justify-between px-4 pt-5 pb-4">
        <Link
          to="/my-settings"
          className="flex items-center gap-2 font-heading text-sm font-medium tracking-tight text-foreground"
        >
          <img src="/logo-mark.png" alt="" className="size-5" />
          jarvis
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <div className="px-2 pb-1">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-sidebar-row-hover"
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
              className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
              activeProps={{
                className: "bg-sidebar-row-hover !text-foreground font-medium",
              }}
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {isDesktop && (
          <div className="mb-3">
            <SectionHeader
              label="Local"
              count={localSessionCount}
              collapsed={prefs.collapsed.local}
              onToggle={() => toggleSection("local")}
            >
              <button
                aria-label="Add project"
                className="mr-1 flex size-5 items-center justify-center rounded text-muted-foreground/70 transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
                onClick={() => void addLocalProject()}
                title="Add project"
                type="button"
              >
                <FolderPlusIcon className="size-3.5" />
              </button>
            </SectionHeader>
            {!prefs.collapsed.local && (
              <>
                {localGroups.map((group) => (
                  <LocalThreadGroup
                    key={group.project.cwd}
                    project={group.project}
                    sessions={group.sessions}
                    activeSessionId={activeLocalSessionId}
                    onNavigate={layout.closeOnMobile}
                    onRemove={() => void removeLocalProject(group.project.cwd)}
                    compact={prefs.compact}
                  />
                ))}
                {localGroups.length === 0 && (
                  <p className="px-2.5 py-3 text-center text-xs text-muted-foreground/70">
                    No projects yet
                  </p>
                )}
              </>
            )}
          </div>
        )}
        {isDesktop && (
          <SectionHeader
            label="Cloud"
            count={
              filteredActive.length +
              (showResolved ? filteredResolved.length : 0)
            }
            collapsed={prefs.collapsed.cloud}
            onToggle={() => toggleSection("cloud")}
          />
        )}
        {!cloudCollapsed && (
          <>
            {prefs.group === "none"
              ? sections[0]?.threads.map((thread) => (
                  <ThreadRow
                    key={thread.id}
                    thread={thread}
                    isActive={thread.id === activeThreadId}
                    onNavigate={layout.closeOnMobile}
                    compact={prefs.compact}
                  />
                ))
              : sections.map((section) => (
                  <ThreadGroup
                    key={`${prefs.group}:${section.key}`}
                    label={section.label}
                    threads={section.threads}
                    activeThreadId={activeThreadId}
                    onNavigate={layout.closeOnMobile}
                    defaultCollapsed={section.defaultCollapsed}
                    compact={prefs.compact}
                  />
                ))}
            {showResolved && (
              <ResolvedThreadGroup
                threads={filteredResolved}
                hasMore={resolvedHasMore}
                activeThreadId={activeThreadId}
                onNavigate={layout.closeOnMobile}
                compact={prefs.compact}
              />
            )}
            {isEmpty && (
              <p className="px-2.5 py-6 text-center text-xs text-muted-foreground/70">
                No threads match these filters.
              </p>
            )}
          </>
        )}
      </div>

      <div className="flex items-center gap-1 p-2">
        <div className="min-w-0 flex-1">
          <SidebarUserMenu user={user} showSettingsLink />
        </div>
        <SidebarFilterMenu
          prefs={prefs}
          facets={facets}
          onGroupChange={setGroup}
          onFiltersChange={setFilters}
          onCompactChange={setCompact}
          onResetFilters={resetFilters}
        />
      </div>
    </SidebarFrame>
  )
}

function SectionHeader({
  label,
  count,
  collapsed,
  onToggle,
  children,
}: {
  label: string
  count: number
  collapsed: boolean
  onToggle: () => void
  children?: ReactNode
}) {
  return (
    <div className="flex items-center">
      <button
        type="button"
        onClick={onToggle}
        className="flex min-w-0 flex-1 items-center gap-1 px-2 py-1.5 text-left font-heading text-xs font-semibold tracking-wide text-foreground uppercase transition-colors hover:text-foreground/80"
        aria-expanded={!collapsed}
      >
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <span className="text-[10px] font-medium text-muted-foreground/70">
          {count}
        </span>
      </button>
      {children}
    </div>
  )
}

function groupLocalProjects(
  projects: Array<DesktopProject>,
  sessions: Array<DesktopAcpSessionSummary>
) {
  const sessionsByProject = new Map<string, Array<DesktopAcpSessionSummary>>()
  for (const session of sessions) {
    const group = sessionsByProject.get(session.cwd) ?? []
    group.push(session)
    sessionsByProject.set(session.cwd, group)
  }
  return projects
    .map((project) => ({
      project,
      sessions: (sessionsByProject.get(project.cwd) ?? []).sort(
        (left, right) => right.updatedAt - left.updatedAt
      ),
      updatedAt: Math.max(
        project.addedAt,
        ...(sessionsByProject.get(project.cwd) ?? []).map(
          (session) => session.updatedAt
        )
      ),
    }))
    .sort((left, right) => right.updatedAt - left.updatedAt)
}

function LocalThreadGroup({
  project,
  sessions,
  activeSessionId,
  onNavigate,
  onRemove,
  compact = false,
}: {
  project: DesktopProject
  sessions: Array<DesktopAcpSessionSummary>
  activeSessionId?: string
  onNavigate?: () => void
  onRemove: () => void
  compact?: boolean
}) {
  const [collapsed, setCollapsed] = useState(false)
  const ToggleIcon = collapsed ? CaretRightIcon : CaretDownIcon

  return (
    <div className={cn("group/project", compact ? "mb-2" : "mb-3")}>
      <div className="flex items-center">
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-1 px-2 py-1 text-left text-[10px] font-medium tracking-wide text-muted-foreground/70 uppercase transition-colors hover:text-muted-foreground"
          aria-expanded={!collapsed}
          title={project.cwd}
        >
          <ToggleIcon className="size-3" />
          <FolderOpenIcon className="size-3.5" />
          <span className="min-w-0 flex-1 truncate">{project.name}</span>
          <span>{sessions.length}</span>
        </button>
        <button
          aria-label={`Remove ${project.name}`}
          className="mr-1 flex size-5 items-center justify-center rounded text-muted-foreground/60 opacity-0 transition-opacity group-hover/project:opacity-100 hover:bg-sidebar-row-hover hover:text-destructive focus:opacity-100 [@media(hover:none)]:opacity-100"
          onClick={onRemove}
          title="Remove project"
          type="button"
        >
          <TrashIcon className="size-3.5" />
        </button>
      </div>
      {!collapsed &&
        sessions.map((session) => (
          <LocalThreadRow
            key={session.id}
            session={session}
            isActive={session.id === activeSessionId}
            onNavigate={onNavigate}
            compact={compact}
          />
        ))}
    </div>
  )
}

function LocalThreadRow({
  session,
  isActive,
  onNavigate,
  compact = false,
}: {
  session: DesktopAcpSessionSummary
  isActive: boolean
  onNavigate?: () => void
  compact?: boolean
}) {
  const running = session.status === "running" || session.status === "starting"
  return (
    <Link
      to="/agents/local/$sessionId"
      params={{ sessionId: session.id }}
      onClick={onNavigate}
      className={cn(
        "mb-0.5 flex items-center gap-2 rounded-lg px-2.5 transition-colors",
        compact ? "h-7 gap-1.5" : "h-8",
        isActive
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-sidebar-row-hover"
      )}
    >
      {running ? (
        <CircleNotchIcon
          className="size-3 shrink-0 animate-spin text-primary"
          aria-label="Local thread running"
        />
      ) : (
        <span className="size-2 shrink-0 rounded-full bg-border" />
      )}
      <span className="min-w-0 flex-1 truncate text-xs">{session.title}</span>
    </Link>
  )
}

function ThreadGroup({
  label,
  threads,
  activeThreadId,
  onNavigate,
  defaultCollapsed = false,
  compact = false,
}: {
  label: string
  threads: Array<AgentThread>
  activeThreadId?: string
  onNavigate?: () => void
  defaultCollapsed?: boolean
  compact?: boolean
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  if (threads.length === 0) return null

  const ToggleIcon = collapsed ? CaretRightIcon : CaretDownIcon

  return (
    <div className={compact ? "mb-2" : "mb-3"}>
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-[10px] font-medium tracking-wide text-muted-foreground/70 uppercase transition-colors hover:text-muted-foreground"
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
            compact={compact}
          />
        ))}
    </div>
  )
}

function ResolvedThreadGroup({
  threads,
  hasMore,
  activeThreadId,
  onNavigate,
  compact = false,
}: {
  threads: Array<AgentThread>
  hasMore: boolean
  activeThreadId?: string
  onNavigate?: () => void
  compact?: boolean
}) {
  const [collapsed, setCollapsed] = useState(true)
  if (threads.length === 0) return null

  const ToggleIcon = collapsed ? CaretRightIcon : CaretDownIcon
  const visible = threads.slice(0, RESOLVED_SIDEBAR_LIMIT)

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center gap-1 px-2 py-1 text-left text-[10px] font-medium tracking-wide text-muted-foreground/70 uppercase transition-colors hover:text-muted-foreground"
        aria-expanded={!collapsed}
      >
        <ToggleIcon className="size-3" />
        <span className="min-w-0 flex-1 truncate">Resolved</span>
        <span>
          {threads.length}
          {hasMore ? "+" : ""}
        </span>
      </button>
      {!collapsed && (
        <>
          {visible.map((thread) => (
            <ThreadRow
              key={thread.id}
              thread={thread}
              isActive={thread.id === activeThreadId}
              onNavigate={onNavigate}
              compact={compact}
            />
          ))}
          {hasMore && (
            <Link
              to="/agents/threads"
              search={{ resolved: true, page: 1 }}
              onClick={onNavigate}
              className="mt-0.5 flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
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
  compact = false,
}: {
  thread: AgentThread
  isActive: boolean
  onNavigate?: () => void
  compact?: boolean
}) {
  const deleteThread = useDeleteAgentThread()
  const resolveThread = useResolveAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const isReadOnly = thread.isOwner === false
  const badge =
    thread.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null
  const isDeleting =
    deleteThread.isPending && deleteThread.variables === thread.id

  const onDelete = (e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
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

  const openSource = () => {
    if (!thread.sourceUrl) return
    window.open(thread.sourceUrl, "_blank", "noopener,noreferrer")
  }

  const copySandboxId = () => {
    if (!thread.sandboxId) return
    void navigator.clipboard.writeText(thread.sandboxId)
  }

  return (
    <>
      <div className={cn("group relative mb-0.5", isDeleting && "opacity-50")}>
        <Link
          to="/agents/$threadId"
          params={{ threadId: thread.id }}
          onClick={onNavigate}
          className={cn(
            "flex items-center gap-2 rounded-lg px-2.5 transition-colors group-hover:pr-8 [@media(hover:none)]:pr-8",
            compact ? "h-7 gap-1.5" : "h-8",
            isActive
              ? "bg-accent text-foreground"
              : "text-muted-foreground group-hover:bg-sidebar-row-hover"
          )}
        >
          {thread.status === "running" ? (
            <CircleNotchIcon
              className="size-3 shrink-0 animate-spin text-primary"
              aria-label="Thread running"
            />
          ) : (
            <span
              className={cn(
                "size-2 shrink-0 rounded-full",
                showFinishedIndicator ? "bg-primary" : "bg-border"
              )}
              aria-label={
                showFinishedIndicator ? "Thread finished" : "Thread viewed"
              }
            />
          )}
          {source && SourceIcon && (
            <SourceIcon
              className="size-3.5 shrink-0 text-muted-foreground/70"
              aria-label={source.label}
            >
              <title>{source.label}</title>
            </SourceIcon>
          )}
          <span className="min-w-0 flex-1 truncate text-xs">
            {thread.title}
          </span>
          {!compact && prMeta && PrIcon && (
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
          {!compact && badge && (
            <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] text-success-foreground group-hover:hidden">
              {badge}
            </span>
          )}
        </Link>
        {/* One actions menu for every input: revealed on hover, kept while
            open, and always shown on devices that can't hover (touch). It sits
            outside the Link so opening it never navigates the row. */}
        <Menu.Root>
          <Menu.Trigger
            render={
              <button
                type="button"
                aria-label="Thread actions"
                className="absolute top-1/2 right-1 hidden size-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground/70 group-hover:flex hover:bg-accent hover:text-foreground data-popup-open:flex [@media(hover:none)]:flex"
              >
                <DotsThreeVerticalIcon className="size-4" weight="bold" />
              </button>
            }
          />
          <Menu.Portal>
            <Menu.Positioner
              align="end"
              sideOffset={4}
              className="z-50 outline-none"
            >
              <Menu.Popup className="min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
                <Menu.Item
                  disabled={!thread.traceUrl}
                  onClick={openTrace}
                  className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                >
                  <TreeStructureIcon className="size-3.5" />
                  Open trace
                </Menu.Item>
                {thread.sourceUrl && (
                  <Menu.Item
                    onClick={openSource}
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                  >
                    <IoLogoSlack className="size-3.5" />
                    Open Slack thread
                  </Menu.Item>
                )}
                <Menu.Item
                  disabled={!thread.sandboxId}
                  onClick={copySandboxId}
                  title={thread.sandboxId ?? undefined}
                  className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                >
                  <CopyIcon className="size-3.5" />
                  Copy sandbox ID
                </Menu.Item>
                {!isReadOnly && (
                  <Menu.Item
                    onClick={() => onToggleResolved()}
                    disabled={resolveThread.isPending}
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                  >
                    {isResolved ? (
                      <ArrowCounterClockwiseIcon className="size-3.5" />
                    ) : (
                      <CheckCircleIcon className="size-3.5" />
                    )}
                    {isResolved ? "Unresolve thread" : "Resolve thread"}
                  </Menu.Item>
                )}
                {!isReadOnly && (
                  <Menu.Item
                    onClick={() => onDelete()}
                    disabled={isDeleting}
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-destructive outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                  >
                    <TrashIcon className="size-3.5" />
                    Delete thread
                  </Menu.Item>
                )}
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
      <Dialog.Root open={deleteOpen} onOpenChange={setDeleteOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <div className="flex flex-col gap-4">
              <Dialog.Title className="text-sm font-medium">
                Delete thread
              </Dialog.Title>
              <Dialog.Description className="text-xs text-muted-foreground">
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
  activeLocalSessionId,
  children,
}: {
  user: SessionUser
  activeThreadId?: string
  activeLocalSessionId?: string
  children: React.ReactNode
}) {
  const layout = useSidebarLayout()
  return (
    <SidebarLayoutProvider value={layout}>
      <div className="agents-ui flex h-svh overflow-hidden bg-background">
        <AgentsSidebar
          user={user}
          activeThreadId={activeThreadId}
          activeLocalSessionId={activeLocalSessionId}
          layout={layout}
        />
        <main className="surface-grain relative flex min-w-0 flex-1 overflow-hidden bg-background">
          {children}
        </main>
      </div>
    </SidebarLayoutProvider>
  )
}
