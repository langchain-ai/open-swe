import { ContextMenu } from "@base-ui/react/context-menu"
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
  GitMergeIcon,
  GitPullRequestIcon,
  LightningIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  PushPinIcon,
  PushPinSlashIcon,
  SparkleIcon,
  TrashIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react"
import { Kanban } from "lucide-react"
import {
  IoCloudOutline,
  IoLaptopOutline,
  IoLogoGithub,
  IoLogoSlack,
} from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { useCallback, useMemo, useState } from "react"
import type { ComponentType, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { AgentSource, AgentThread } from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import type { SidebarLayout } from "@/components/sidebar-layout"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { SidebarFilterMenu } from "@/features/agents/components/SidebarFilterMenu"
import { SidebarProjectSelector } from "@/features/agents/components/SidebarProjectSelector"
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
  hasActiveFilters,
} from "@/features/agents/lib/sidebarFilter"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  useDeleteAgentThread,
  usePinAgentThread,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarThreads,
} from "@/features/agents/lib/queries"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import {
  useDesktopLocalThreads,
  useLocalThreadActivity,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import {
  cloudSidebarThread,
  filterSidebarProject,
  localSidebarThread,
  sidebarProjectOptions,
  sortSidebarThreads,
} from "@/features/agents/lib/sidebarThreads"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import { cn } from "@/lib/utils"

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

function openContextMenuFromKeyboard(
  event: React.KeyboardEvent<HTMLAnchorElement>
) {
  if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) {
    return
  }
  event.preventDefault()
  const rect = event.currentTarget.getBoundingClientRect()
  event.currentTarget.dispatchEvent(
    new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
    })
  )
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
  const { prefs, setCompact, setFilters, resetFilters } = useSidebarPrefs()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const sidebar = useSidebarThreads({
    activeThreadId,
    includeAutomations:
      prefs.filters.includeAutomations ||
      prefs.filters.sources.includes("schedule"),
    includeResolved: prefs.filters.includeResolved,
    enabled: !localOnly,
  })
  const localThreads = useDesktopLocalThreads({ enabled: isDesktop })
  const localSessions = localThreads.data ?? []
  const activity = useLocalThreadActivity()
  const refreshLocalThreads = useRefreshLocalThreads()
  const {
    projects: localProjects,
    addProject: addLocalProject,
    removeProject: removeLocalProject,
  } = useDesktopProjects()
  const [selectedProjectKey, setSelectedProjectKey] = useState<string | null>(
    null
  )
  const pinnedThreads = sidebar.data.pinned ?? []
  const pinnedIds = new Set(pinnedThreads.map((thread) => thread.id))
  const activeThreads = sidebar.data.active.items.filter(
    (thread) => !pinnedIds.has(thread.id)
  )
  const resolvedThreads = sidebar.data.resolved.items.filter(
    (thread) => !pinnedIds.has(thread.id)
  )
  const activeHasMore = sidebar.data.active.hasMore
  const resolvedHasMore = sidebar.data.resolved.hasMore
  const visibleThreads = [
    ...pinnedThreads,
    ...activeThreads,
    ...resolvedThreads,
  ]
  useSeedAgentThreadDetails(visibleThreads, activeThreadId)
  useRunCompletionNotifier(visibleThreads, activeThreadId, openThread)

  const projectByPath = new Map(
    localProjects.map((project) => [project.cwd, project])
  )
  const pinnedItems = pinnedThreads.map(cloudSidebarThread)
  const threadItems: Array<SidebarThreadItem> = [
    ...activeThreads.map(cloudSidebarThread),
    ...resolvedThreads.map(cloudSidebarThread),
    ...localSessions.map((thread) =>
      localSidebarThread(
        thread,
        projectByPath.get(thread.cwd),
        activity[thread.id]
      )
    ),
  ]
  const allItems = [...pinnedItems, ...threadItems]
  const projects = sidebarProjectOptions(allItems, localProjects)
  const activeProjectKey = projects.some(
    (project) => project.key === selectedProjectKey
  )
    ? selectedProjectKey
    : null
  const filteredPinnedItems = filterSidebarProject(
    filterThreads(pinnedItems, prefs.filters),
    activeProjectKey
  )
  const filteredThreadItems = sortSidebarThreads(
    filterSidebarProject(
      filterThreads(threadItems, prefs.filters),
      activeProjectKey
    )
  )
  const loadedFacets = availableFacets(allItems)
  const facets = {
    models: [
      ...new Set([...prefs.filters.models, ...loadedFacets.models]),
    ].sort((a, b) => a.localeCompare(b)),
  }
  const cloudPending = !localOnly && sidebar.isPending
  const resolvedLoading =
    !localOnly &&
    !sidebar.isPending &&
    prefs.filters.includeResolved &&
    sidebar.resolvedQuery.isLoading
  const sourcesLoading = cloudPending || (isDesktop && localThreads.isPending)
  const isEmpty =
    !cloudPending &&
    (!isDesktop || !localThreads.isPending) &&
    !resolvedLoading &&
    filteredPinnedItems.length === 0 &&
    filteredThreadItems.length === 0
  const activeKey = activeLocalSessionId
    ? `local:${activeLocalSessionId}`
    : activeThreadId
      ? `cloud:${activeThreadId}`
      : undefined

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
        </Link>
      </div>

      {!localOnly && (
        <nav
          className={cn(
            "flex flex-col gap-0.5 px-2",
            isDesktop ? "pb-3" : "pb-4"
          )}
        >
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
        <SidebarProjectSelector
          projects={projects}
          localProjects={isDesktop ? localProjects : undefined}
          selectedProjectKey={activeProjectKey}
          onSelectProject={setSelectedProjectKey}
          onAddProject={isDesktop ? () => void addLocalProject() : undefined}
          onRemoveProject={
            isDesktop ? (cwd) => void removeLocalProject(cwd) : undefined
          }
        />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {sourcesLoading && allItems.length === 0 && (
            <ThreadListSkeleton compact={prefs.compact} />
          )}
          {sidebar.isError && (
            <ThreadSourceError
              label="Cloud threads unavailable"
              onRetry={() => void sidebar.refetch()}
            />
          )}
          {localThreads.isError && (
            <ThreadSourceError
              label="Local threads unavailable"
              onRetry={() => void localThreads.refetch()}
            />
          )}
          {sourcesLoading && allItems.length > 0 && (
            <div className="flex items-center gap-1.5 px-2.5 py-2 text-xs text-muted-foreground/70">
              <CircleNotchIcon className="size-3.5 animate-spin" />
              Loading threads…
            </div>
          )}
          {filteredPinnedItems.length > 0 && (
            <ThreadGroup
              label="Pinned"
              threads={filteredPinnedItems}
              activeKey={activeKey}
              onNavigate={layout.closeOnMobile}
              onDeleteLocal={refreshLocalThreads}
              compact={prefs.compact}
            />
          )}
          {filteredThreadItems.map((thread) => (
            <ThreadRow
              key={thread.key}
              item={thread}
              isActive={thread.key === activeKey}
              onNavigate={layout.closeOnMobile}
              onDeleteLocal={refreshLocalThreads}
              compact={prefs.compact}
            />
          ))}
          {!sidebar.isPending && activeHasMore && (
            <LoadMoreThreadsButton
              label="Load more cloud threads"
              loading={sidebar.activeQuery.isFetchingNextPage}
              onClick={() => void sidebar.activeQuery.fetchNextPage()}
            />
          )}
          {!sidebar.isPending &&
            prefs.filters.includeResolved &&
            resolvedHasMore && (
              <LoadMoreThreadsButton
                label="Load more resolved cloud threads"
                loading={sidebar.resolvedQuery.isFetchingNextPage}
                onClick={() => void sidebar.resolvedQuery.fetchNextPage()}
              />
            )}
          {resolvedLoading && (
            <div className="flex items-center gap-1.5 px-2.5 py-2 text-xs text-muted-foreground/70">
              <CircleNotchIcon className="size-3.5 animate-spin" />
              Loading resolved threads…
            </div>
          )}
          {isEmpty && !sidebar.isError && !localThreads.isError && (
            <p className="px-2.5 py-6 text-center text-xs text-muted-foreground/70">
              {activeProjectKey
                ? "No threads in this project."
                : hasActiveFilters(prefs.filters)
                  ? "No threads match these filters."
                  : "No threads yet."}
            </p>
          )}
        </div>
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
        <SidebarFilterMenu
          prefs={prefs}
          facets={facets}
          onFiltersChange={setFilters}
          onCompactChange={setCompact}
          onResetFilters={resetFilters}
        />
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
  activeKey,
  onNavigate,
  onDeleteLocal,
  compact = false,
}: {
  label: string
  threads: Array<SidebarThreadItem>
  activeKey?: string
  onNavigate?: () => void
  onDeleteLocal: (threadId?: string) => void
  compact?: boolean
}) {
  const [collapsed, setCollapsed] = useState(false)
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
      {!collapsed && (
        <>
          {threads.map((thread) => (
            <ThreadRow
              key={thread.key}
              item={thread}
              isActive={thread.key === activeKey}
              onNavigate={onNavigate}
              onDeleteLocal={onDeleteLocal}
              compact={compact}
              pinned={label === "Pinned"}
            />
          ))}
        </>
      )}
    </div>
  )
}

function ThreadSourceError({
  label,
  onRetry,
}: {
  label: string
  onRetry: () => void
}) {
  return (
    <div className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <button
        type="button"
        className="shrink-0 font-medium text-foreground hover:underline"
        onClick={onRetry}
      >
        Retry
      </button>
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
  item,
  isActive,
  onNavigate,
  onDeleteLocal,
  compact = false,
  pinned = false,
}: {
  item: SidebarThreadItem
  isActive: boolean
  onNavigate?: () => void
  onDeleteLocal: (threadId?: string) => void
  compact?: boolean
  pinned?: boolean
}) {
  const navigate = useNavigate()
  const deleteThread = useDeleteAgentThread()
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deletingLocal, setDeletingLocal] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const thread = item.location === "cloud" ? item.thread : null
  const badge =
    thread?.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null
  const isDeleting =
    deletingLocal ||
    (item.location === "cloud" &&
      deleteThread.isPending &&
      deleteThread.variables === item.id)

  const onDelete = (e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (isDeleting) return
    setDeleteOpen(true)
  }

  const onConfirmDelete = async () => {
    if (isDeleting) return
    if (item.location === "cloud") {
      deleteThread.mutate(item.id, {
        onSuccess: () => setDeleteOpen(false),
      })
      return
    }
    setDeletingLocal(true)
    setDeleteError(null)
    try {
      const deleted =
        (await window.openSweDesktop?.deleteLocalThread(item.id)) ?? false
      if (deleted) {
        onDeleteLocal(item.id)
        setDeleteOpen(false)
        if (isActive) {
          onNavigate?.()
          void navigate({ to: "/agents" })
        }
      } else {
        setDeleteError("Local Open SWE thread not found")
      }
    } catch (error) {
      setDeleteError(
        error instanceof Error ? error.message : "Could not delete local thread"
      )
    }
    setDeletingLocal(false)
  }

  const onTogglePinned = () => {
    if (!thread || pinThread.isPending) return
    pinThread.mutate({ threadId: thread.id, pinned: !pinned })
  }

  const isResolved = thread?.resolved === true
  const onToggleResolved = (e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!thread || resolveThread.isPending) return
    resolveThread.mutate({ threadId: thread.id, resolved: !isResolved })
  }

  const source =
    thread?.source && thread.source !== "dashboard"
      ? SOURCE_META[thread.source]
      : null
  const SourceIcon = source?.icon
  const prMeta = thread?.pr ? PR_STATE_META[thread.pr.state] : null
  const PrIcon = prMeta?.icon
  const isAutomation =
    thread?.threadCategory === "automation" || thread?.source === "schedule"
  const showFinishedIndicator = item.status === "finished" && !item.viewed

  const copySandboxId = () => {
    if (!thread?.sandboxId) return
    void navigator.clipboard.writeText(thread.sandboxId)
  }

  const handleNavigate = (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (contextMenuOpen) {
      event.preventDefault()
      return
    }
    onNavigate?.()
  }

  const rowClassName = cn(
    "flex items-center gap-2 rounded-lg px-2.5 transition-colors",
    compact ? "h-7 gap-1.5" : "h-8",
    isActive
      ? thread?.adminThread
        ? "bg-destructive/10 text-foreground"
        : "bg-accent text-foreground"
      : thread?.adminThread
        ? "bg-destructive/5 text-muted-foreground group-hover:bg-destructive/10"
        : "text-muted-foreground group-hover:bg-sidebar-row-hover"
  )

  const rowContent = (
    <>
      {pinned && (
        <PushPinIcon
          className="size-3 shrink-0 text-primary"
          aria-label="Pinned thread"
        />
      )}
      {item.status === "running" ? (
        <CircleNotchIcon
          className="size-3 shrink-0 animate-spin text-primary"
          aria-label="Thread running"
        />
      ) : (
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            item.status === "error"
              ? "bg-destructive"
              : showFinishedIndicator
                ? "bg-primary"
                : "bg-border"
          )}
          aria-label={
            item.status === "error"
              ? "Thread error"
              : showFinishedIndicator
                ? "Thread finished"
                : "Thread viewed"
          }
        />
      )}
      {item.location === "local" ? (
        <span title="This Mac" className="flex shrink-0">
          <IoLaptopOutline
            className="size-3.5 text-muted-foreground/70"
            aria-label="This Mac"
          />
        </span>
      ) : (
        <span title="Cloud" className="flex shrink-0">
          <IoCloudOutline
            className="size-3.5 text-muted-foreground/70"
            aria-label="Cloud"
          />
        </span>
      )}
      {source && SourceIcon && (
        <SourceIcon
          className="size-3.5 shrink-0 text-muted-foreground/70"
          aria-label={source.label}
        >
          <title>{source.label}</title>
        </SourceIcon>
      )}
      <span className="min-w-0 flex-1 truncate text-[13px]">{item.title}</span>
      {thread?.automationActionPosted && (
        <IoLogoSlack
          className="size-3.5 shrink-0 text-success-foreground"
          aria-label="Action posted to Slack"
        >
          <title>Action posted to Slack</title>
        </IoLogoSlack>
      )}
      {!compact && isAutomation && (
        <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground">
          Automation
        </span>
      )}
      {!compact && prMeta && PrIcon && (
        <PrIcon
          className={cn("size-3.5 shrink-0", prMeta.className)}
          aria-label={prMeta.label}
        >
          <title>{prMeta.label}</title>
        </PrIcon>
      )}
      {!compact && badge && (
        <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] text-success-foreground">
          {badge}
        </span>
      )}
    </>
  )

  return (
    <>
      <ContextMenu.Root onOpenChange={setContextMenuOpen}>
        <ContextMenu.Trigger
          className={cn("group relative mb-0.5", isDeleting && "opacity-50")}
        >
          {item.location === "cloud" ? (
            <Link
              to="/agents/$threadId"
              params={{ threadId: item.id }}
              onClick={handleNavigate}
              onKeyDown={openContextMenuFromKeyboard}
              className={rowClassName}
            >
              {rowContent}
            </Link>
          ) : (
            <Link
              to="/agents/local/$sessionId"
              params={{ sessionId: item.id }}
              onClick={handleNavigate}
              onKeyDown={openContextMenuFromKeyboard}
              className={rowClassName}
            >
              {rowContent}
            </Link>
          )}
        </ContextMenu.Trigger>
        <ContextMenu.Portal>
          <ContextMenu.Positioner className="z-50 outline-none">
            <ContextMenu.Popup className="min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
              {thread?.traceUrl && (
                <ContextMenu.LinkItem
                  href={thread.traceUrl}
                  target="_blank"
                  rel="noreferrer"
                  closeOnClick
                  className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
                >
                  <TreeStructureIcon className="size-3.5" />
                  Open trace
                </ContextMenu.LinkItem>
              )}
              {thread?.sourceUrl && (
                <ContextMenu.LinkItem
                  href={thread.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  closeOnClick
                  className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
                >
                  <IoLogoSlack className="size-3.5" />
                  Open Slack thread
                </ContextMenu.LinkItem>
              )}
              {thread && (
                <>
                  <ContextMenu.Item
                    onClick={onTogglePinned}
                    disabled={pinThread.isPending}
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                  >
                    {pinned ? (
                      <PushPinSlashIcon className="size-3.5" />
                    ) : (
                      <PushPinIcon className="size-3.5" />
                    )}
                    {pinned ? "Unpin thread" : "Pin thread"}
                  </ContextMenu.Item>
                  <ContextMenu.Item
                    disabled={!thread.sandboxId}
                    onClick={copySandboxId}
                    title={thread.sandboxId ?? undefined}
                    className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
                  >
                    <CopyIcon className="size-3.5" />
                    Copy sandbox ID
                  </ContextMenu.Item>
                  <ContextMenu.Item
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
                  </ContextMenu.Item>
                </>
              )}
              <ContextMenu.Item
                onClick={() => onDelete()}
                disabled={isDeleting}
                className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-destructive outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                <TrashIcon className="size-3.5" />
                Delete thread
              </ContextMenu.Item>
            </ContextMenu.Popup>
          </ContextMenu.Positioner>
        </ContextMenu.Portal>
      </ContextMenu.Root>
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open)
          if (!open) setDeleteError(null)
        }}
        threadTitle={item.title}
        isDeleting={isDeleting}
        onConfirm={() => void onConfirmDelete()}
        detail={
          item.location === "local"
            ? "This removes its history but does not revert changes made to your project."
            : undefined
        }
        error={deleteError}
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
