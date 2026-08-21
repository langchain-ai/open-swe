import { Menu } from "@base-ui/react/menu"
import { Dialog } from "@base-ui/react/dialog"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  ArrowCounterClockwiseIcon,
  CalendarBlankIcon,
  CaretDownIcon,
  CaretRightIcon,
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
  MagnifyingGlassIcon,
  PlusIcon,
  SparkleIcon,
  TrashIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react"
import { Kanban } from "lucide-react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { ComponentType, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { DesktopLocalThreadSummary, DesktopProject } from "@/desktop"
import type { AgentSource, AgentThread } from "@/features/agents/lib/types"
import type { SidebarLayout } from "@/components/sidebar-layout"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { DesktopThreadSourceToggle } from "@/features/agents/components/DesktopThreadSourceToggle"
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
import {
  useDesktopLocalThreads,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import { useDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { Kbd } from "@/components/ui/kbd"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useAppCommand,
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import { useShortcutLabel } from "@/lib/hotkeys"
import { cn } from "@/lib/utils"

function SidebarShortcut({ commandId }: { commandId: string }) {
  const shortcut = useAppCommand(commandId)?.shortcuts?.[0] ?? ""
  const label = useShortcutLabel(shortcut)
  if (!shortcut) return null
  return (
    <Kbd className="ml-auto h-4 min-w-4 bg-transparent px-0 text-[10px]">
      {label}
    </Kbd>
  )
}

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
  user: SessionUser | null
  localOnly?: boolean
  activeThreadId?: string
  activeLocalSessionId?: string
  layout: SidebarLayout
}

const NAV = [
  { to: "/agents/threads", label: "Kanban", icon: Kanban },
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/agents/reviews", label: "Reviews", icon: GitPullRequestIcon },
] as const

export function AgentsSidebar({
  user,
  localOnly = false,
  activeThreadId,
  activeLocalSessionId,
  layout,
}: AgentsSidebarProps) {
  const navigate = useNavigate()
  const { openPalette } = useAppCommandControls()
  const openThread = useCallback(
    (threadId: string) => {
      void navigate({ to: "/agents/$threadId", params: { threadId } })
    },
    [navigate]
  )
  const { prefs, setGroup, setCompact, setFilters, resetFilters } =
    useSidebarPrefs()
  const sidebar = useSidebarThreads({
    activeThreadId,
    includeAutomations:
      prefs.filters.includeAutomations ||
      prefs.filters.sources.includes("schedule"),
    includeResolved: prefs.filters.includeResolved,
    enabled: !localOnly,
  })
  const localSessions = useDesktopLocalThreads().data ?? []
  const refreshLocalThreads = useRefreshLocalThreads()
  const deleteLocalSession = async (sessionId: string) => {
    const deleted =
      (await window.openSweDesktop?.deleteLocalThread(sessionId)) ?? false
    if (deleted) refreshLocalThreads()
    return deleted
  }
  const {
    projects: localProjects,
    addProject: addLocalProject,
    removeProject: removeLocalProject,
  } = useDesktopProjects()
  const localGroups = groupLocalProjects(localProjects, localSessions)
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const [desktopThreadSource, setDesktopThreadSource] = useDesktopThreadSource()
  useEffect(() => {
    if (!isDesktop) return
    if (activeLocalSessionId) setDesktopThreadSource("local")
    else if (activeThreadId) setDesktopThreadSource("cloud")
  }, [activeLocalSessionId, activeThreadId, isDesktop, setDesktopThreadSource])
  const activeThreads = sidebar.data.active.items
  const resolvedThreads = sidebar.data.resolved.items
  const activeHasMore = sidebar.data.active.hasMore
  const resolvedHasMore = sidebar.data.resolved.hasMore
  const visibleThreads = [...activeThreads, ...resolvedThreads]
  useSeedAgentThreadDetails(visibleThreads, activeThreadId)
  useRunCompletionNotifier(visibleThreads, activeThreadId, openThread)

  const loadedFacets = availableFacets(visibleThreads)
  const facets = {
    models: [
      ...new Set([...prefs.filters.models, ...loadedFacets.models]),
    ].sort((a, b) => a.localeCompare(b)),
    repos: [...new Set([...prefs.filters.repos, ...loadedFacets.repos])].sort(
      (a, b) => a.localeCompare(b)
    ),
  }
  const filteredActive = filterThreads(activeThreads, prefs.filters)
  const filteredResolved = filterThreads(resolvedThreads, prefs.filters)
  const showResolved = prefs.filters.includeResolved
  const groupedThreads =
    prefs.group === "focus" && showResolved
      ? [...filteredActive, ...filteredResolved]
      : filteredActive
  const sections = groupThreadsByMode(groupedThreads, prefs.group)
  const resolvedLoading =
    !sidebar.isPending && showResolved && sidebar.resolvedQuery.isLoading
  const isCloudEmpty =
    !sidebar.isPending &&
    !resolvedLoading &&
    sections.length === 0 &&
    (!showResolved || filteredResolved.length === 0) &&
    hasActiveFilters(prefs.filters)
  const cloudActivity = {
    running: activeThreads.filter((thread) => thread.status === "running")
      .length,
    completed: activeThreads.filter(
      (thread) => thread.status === "finished" && !thread.viewed
    ).length,
  }
  const localActivity = {
    running: localSessions.filter(
      (thread) => thread.status === "running" || thread.status === "starting"
    ).length,
    completed: localSessions.filter(
      (thread) =>
        !thread.viewed &&
        thread.status !== "running" &&
        thread.status !== "starting"
    ).length,
  }
  const showLocalThreads =
    isDesktop && (localOnly || desktopThreadSource === "local")
  const showCloudThreads =
    !localOnly && (!isDesktop || desktopThreadSource === "cloud")

  return (
    <SidebarFrame {...layout} className="border-r border-border bg-sidebar">
      <div
        className={cn(
          "flex items-center justify-between px-4 pb-4",
          isDesktop ? "pt-13" : "pt-5"
        )}
      >
        <Link
          to={localOnly ? "/agents" : "/my-settings"}
          className="flex items-center gap-2 font-heading text-sm font-medium tracking-tight text-foreground"
        >
          <img src="/logo-mark.png" alt="" className="size-5" />
          Open SWE
        </Link>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Search"
            title="Search"
            onClick={() => {
              layout.closeOnMobile()
              openPalette()
            }}
            className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <MagnifyingGlassIcon className="size-4" />
          </button>
          <SidebarCollapseButton onToggle={layout.toggle} />
        </div>
      </div>

      <div className="flex flex-col gap-0.5 px-2 pb-1">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium text-foreground transition-colors hover:bg-sidebar-row-hover"
        >
          <PlusIcon className="size-4" />
          New Thread
          <SidebarShortcut commandId="new-thread" />
        </Link>
      </div>

      {!localOnly && (
        <nav className="flex flex-col gap-0.5 px-2 pb-4">
          {NAV.map((item) => {
            const Icon = item.icon
            return (
              <Link
                key={item.to}
                to={item.to}
                onClick={layout.closeOnMobile}
                className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
                activeProps={{
                  className:
                    "bg-sidebar-row-hover !text-foreground font-medium",
                }}
              >
                <Icon className="size-4" />
                {item.label}
              </Link>
            )
          })}
        </nav>
      )}

      <div className="flex min-h-0 flex-1 flex-col px-2 pb-2">
        {isDesktop && !localOnly && (
          <DesktopThreadSourceToggle
            source={desktopThreadSource}
            localActivity={localActivity}
            cloudActivity={cloudActivity}
            onSourceChange={setDesktopThreadSource}
          />
        )}
        {showLocalThreads && (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="mb-1 flex items-center px-2 py-1">
              <span className="min-w-0 flex-1 truncate font-heading text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                Projects and threads
              </span>
              <button
                aria-label="Add project"
                className="flex size-5 items-center justify-center rounded text-muted-foreground/70 transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
                onClick={() => void addLocalProject()}
                title="Add project"
                type="button"
              >
                <FolderPlusIcon className="size-3.5" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {localGroups.map((group) => (
                <LocalThreadGroup
                  key={group.project.cwd}
                  project={group.project}
                  sessions={group.sessions}
                  activeSessionId={activeLocalSessionId}
                  onNavigate={layout.closeOnMobile}
                  onDelete={deleteLocalSession}
                  onRemove={() => void removeLocalProject(group.project.cwd)}
                  compact={prefs.compact}
                />
              ))}
              {localGroups.length === 0 && (
                <p className="px-2.5 py-3 text-center text-xs text-muted-foreground/70">
                  No projects yet
                </p>
              )}
            </div>
          </div>
        )}
        {showCloudThreads && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            {sidebar.isPending && (
              <ThreadListSkeleton compact={prefs.compact} />
            )}
            {!sidebar.isPending &&
              (prefs.group === "none"
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
                      hasMore={
                        prefs.group === "focus" && section.key === "done"
                          ? resolvedHasMore
                          : false
                      }
                      count={
                        prefs.group === "focus" && section.key === "done"
                          ? filteredResolved.length
                          : section.threads.length
                      }
                    />
                  )))}
            {!sidebar.isPending && activeHasMore && (
              <LoadMoreThreadsButton
                label="Load more threads"
                loading={sidebar.activeQuery.isFetchingNextPage}
                onClick={() => void sidebar.activeQuery.fetchNextPage()}
              />
            )}
            {!sidebar.isPending &&
              showResolved &&
              prefs.group === "focus" &&
              resolvedHasMore && (
                <LoadMoreThreadsButton
                  label="Load more resolved threads"
                  loading={sidebar.resolvedQuery.isFetchingNextPage}
                  onClick={() => void sidebar.resolvedQuery.fetchNextPage()}
                />
              )}
            {showResolved && prefs.group !== "focus" && (
              <ResolvedThreadGroup
                threads={filteredResolved}
                hasMore={resolvedHasMore}
                activeThreadId={activeThreadId}
                onNavigate={layout.closeOnMobile}
                compact={prefs.compact}
                onLoadMore={() => void sidebar.resolvedQuery.fetchNextPage()}
                isLoadingMore={sidebar.resolvedQuery.isFetchingNextPage}
              />
            )}
            {resolvedLoading && (
              <div className="flex items-center gap-1.5 px-2.5 py-2 text-xs text-muted-foreground/70">
                <CircleNotchIcon className="size-3.5 animate-spin" />
                Loading resolved threads…
              </div>
            )}
            {isCloudEmpty && (
              <p className="px-2.5 py-6 text-center text-xs text-muted-foreground/70">
                No threads match these filters.
              </p>
            )}
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 p-2">
        <div className="min-w-0 flex-1">
          {user ? (
            <SidebarUserMenu user={user} showSettingsLink />
          ) : (
            <Link
              to="/login"
              className="flex w-full items-center justify-center rounded-md border border-border px-2 py-1.5 text-xs font-medium hover:bg-sidebar-accent"
            >
              Sign in for cloud mode
            </Link>
          )}
        </div>
        {showCloudThreads && (
          <SidebarFilterMenu
            prefs={prefs}
            facets={facets}
            onGroupChange={setGroup}
            onFiltersChange={setFilters}
            onCompactChange={setCompact}
            onResetFilters={resetFilters}
          />
        )}
      </div>
    </SidebarFrame>
  )
}

function DeleteThreadDialog({
  open,
  onOpenChange,
  threadTitle,
  isDeleting,
  onConfirm,
  detail = "This cannot be undone.",
  error,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  threadTitle: string
  isDeleting: boolean
  onConfirm: () => void
  detail?: string
  error?: string | null
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex flex-col gap-4">
            <Dialog.Title className="text-sm font-medium">
              Delete thread
            </Dialog.Title>
            <Dialog.Description className="text-xs text-muted-foreground">
              Delete "{threadTitle}"? {detail}
            </Dialog.Description>
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={onConfirm}
                disabled={isDeleting}
              >
                {isDeleting ? "Deleting..." : "Delete"}
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function groupLocalProjects(
  projects: Array<DesktopProject>,
  sessions: Array<DesktopLocalThreadSummary>
) {
  const sessionsByProject = new Map<string, Array<DesktopLocalThreadSummary>>()
  for (const session of sessions) {
    const group = sessionsByProject.get(session.cwd) ?? []
    group.push(session)
    sessionsByProject.set(session.cwd, group)
  }
  return projects
    .map((project) => ({
      project,
      sessions: (sessionsByProject.get(project.cwd) ?? []).sort(
        (left, right) => right.createdAt - left.createdAt
      ),
    }))
    .sort((left, right) => right.project.addedAt - left.project.addedAt)
}

function LocalThreadGroup({
  project,
  sessions,
  activeSessionId,
  onNavigate,
  onDelete,
  onRemove,
  compact = false,
}: {
  project: DesktopProject
  sessions: Array<DesktopLocalThreadSummary>
  activeSessionId?: string
  onNavigate?: () => void
  onDelete: (sessionId: string) => Promise<boolean>
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
            onDelete={onDelete}
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
  onDelete,
  compact = false,
}: {
  session: DesktopLocalThreadSummary
  isActive: boolean
  onNavigate?: () => void
  onDelete: (sessionId: string) => Promise<boolean>
  compact?: boolean
}) {
  const navigate = useNavigate()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const running = session.status === "running" || session.status === "starting"

  const confirmDelete = async () => {
    if (isDeleting) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      if (!(await onDelete(session.id))) {
        throw new Error("Local Open SWE thread not found")
      }
      setDeleteOpen(false)
      if (isActive) {
        onNavigate?.()
        void navigate({ to: "/agents" })
      }
    } catch (error) {
      setDeleteError(
        error instanceof Error ? error.message : "Could not delete local thread"
      )
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <>
      <div className={cn("group relative mb-0.5", isDeleting && "opacity-50")}>
        <Link
          to="/agents/local/$sessionId"
          params={{ sessionId: session.id }}
          onClick={onNavigate}
          className={cn(
            "flex items-center gap-2 rounded-lg px-2.5 transition-colors group-hover:pr-8 [@media(hover:none)]:pr-8",
            compact ? "h-7 gap-1.5" : "h-8",
            isActive
              ? "bg-accent text-foreground"
              : "text-muted-foreground group-hover:bg-sidebar-row-hover"
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
          <span className="min-w-0 flex-1 truncate text-[13px]">
            {session.title}
          </span>
        </Link>
        <Menu.Root>
          <Menu.Trigger
            render={
              <button
                type="button"
                aria-label="Local thread actions"
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
                  onClick={() => setDeleteOpen(true)}
                  disabled={isDeleting}
                  className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-destructive outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                >
                  <TrashIcon className="size-3.5" />
                  Delete thread
                </Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open)
          if (!open) setDeleteError(null)
        }}
        threadTitle={session.title}
        isDeleting={isDeleting}
        onConfirm={() => void confirmDelete()}
        detail="This removes its history but does not revert changes made to your project."
        error={deleteError}
      />
    </>
  )
}

/**
 * Mirrors the grouped thread list's shape so the sidebar reads as loading
 * rather than as an account with no threads. Widths vary per row because a
 * column of identical bars reads as a UI element, not as pending content.
 */
function ThreadListSkeleton({ compact = false }: { compact?: boolean }) {
  const groups = [
    [90, 64, 76],
    [72, 84],
  ]
  return (
    <div data-testid="sidebar-threads-skeleton">
      <span className="sr-only" role="status">
        Loading threads
      </span>
      {groups.map((widths, groupIndex) => (
        <div key={groupIndex} className={compact ? "mb-2" : "mb-3"} aria-hidden>
          <div className="flex items-center gap-1 px-2 py-1">
            <Skeleton className="h-2 w-16 rounded-sm" />
          </div>
          {widths.map((width, rowIndex) => (
            <div
              key={rowIndex}
              className={cn(
                "mb-0.5 flex items-center gap-2 px-2.5",
                compact ? "h-7 gap-1.5" : "h-8"
              )}
            >
              <Skeleton className="size-3 shrink-0 rounded-full" />
              <Skeleton className="h-2.5" style={{ width: `${width}%` }} />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function ThreadGroup({
  label,
  threads,
  activeThreadId,
  onNavigate,
  defaultCollapsed = false,
  compact = false,
  hasMore = false,
  count = threads.length,
}: {
  label: string
  threads: Array<AgentThread>
  activeThreadId?: string
  onNavigate?: () => void
  defaultCollapsed?: boolean
  compact?: boolean
  hasMore?: boolean
  count?: number
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
        <span>
          {count}
          {hasMore ? "+" : ""}
        </span>
      </button>
      {!collapsed && (
        <>
          {threads.map((thread) => (
            <ThreadRow
              key={thread.id}
              thread={thread}
              isActive={thread.id === activeThreadId}
              onNavigate={onNavigate}
              compact={compact}
            />
          ))}
        </>
      )}
    </div>
  )
}

function ResolvedThreadGroup({
  threads,
  hasMore,
  activeThreadId,
  onNavigate,
  compact = false,
  onLoadMore,
  isLoadingMore,
}: {
  threads: Array<AgentThread>
  hasMore: boolean
  activeThreadId?: string
  onNavigate?: () => void
  compact?: boolean
  onLoadMore: () => void
  isLoadingMore: boolean
}) {
  const [collapsed, setCollapsed] = useState(true)
  if (threads.length === 0 && !hasMore) return null

  const ToggleIcon = collapsed ? CaretRightIcon : CaretDownIcon

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
          {threads.map((thread) => (
            <ThreadRow
              key={thread.id}
              thread={thread}
              isActive={thread.id === activeThreadId}
              onNavigate={onNavigate}
              compact={compact}
            />
          ))}
          {hasMore && (
            <LoadMoreThreadsButton
              label="Load more resolved threads"
              loading={isLoadingMore}
              onClick={onLoadMore}
            />
          )}
        </>
      )}
    </div>
  )
}

function LoadMoreThreadsButton({
  label,
  loading,
  onClick,
}: {
  label: string
  loading: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className="mt-0.5 flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left text-[13px] text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground disabled:cursor-wait disabled:opacity-60"
    >
      {loading && <CircleNotchIcon className="size-3.5 animate-spin" />}
      {loading ? "Loading…" : label}
    </button>
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
  const isAutomation =
    thread.threadCategory === "automation" || thread.source === "schedule"
  const showFinishedIndicator = thread.status === "finished" && !thread.viewed

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
              ? thread.adminThread
                ? "bg-destructive/10 text-foreground"
                : "bg-accent text-foreground"
              : thread.adminThread
                ? "bg-destructive/5 text-muted-foreground group-hover:bg-destructive/10"
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
          <span className="min-w-0 flex-1 truncate text-[13px]">
            {thread.title}
          </span>
          {!compact && isAutomation && (
            <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground group-hover:hidden">
              Automation
            </span>
          )}
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
                {thread.traceUrl && (
                  <Menu.LinkItem
                    href={thread.traceUrl}
                    target="_blank"
                    rel="noreferrer"
                    closeOnClick
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
                  >
                    <TreeStructureIcon className="size-3.5" />
                    Open trace
                  </Menu.LinkItem>
                )}
                {thread.sourceUrl && (
                  <Menu.LinkItem
                    href={thread.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    closeOnClick
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
                  >
                    <IoLogoSlack className="size-3.5" />
                    Open Slack thread
                  </Menu.LinkItem>
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
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={thread.title}
        isDeleting={isDeleting}
        onConfirm={onConfirmDelete}
      />
    </>
  )
}

export function AgentsShell({
  user,
  localOnly = false,
  activeThreadId,
  activeLocalSessionId,
  children,
}: {
  user: SessionUser | null
  localOnly?: boolean
  activeThreadId?: string
  activeLocalSessionId?: string
  children: React.ReactNode
}) {
  const layout = useSidebarLayout()
  const sidebarCommands = useMemo(
    () => [
      {
        id: "toggle-sidebar",
        label: "Toggle sidebar",
        aliases: ["show sidebar", "hide sidebar"],
        shortcuts: ["mod+b"],
        group: "Workspace",
        run: layout.toggle,
        desktopId: "toggle-sidebar" as const,
        desktopShortcuts: ["mod+b"],
      },
    ],
    [layout.toggle]
  )
  useRegisterAppCommands(sidebarCommands)

  return (
    <SidebarLayoutProvider value={layout}>
      <div className="agents-ui flex h-svh overflow-hidden bg-background">
        <AgentsSidebar
          user={user}
          localOnly={localOnly}
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
