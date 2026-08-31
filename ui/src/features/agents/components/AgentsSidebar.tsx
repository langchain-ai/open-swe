import { Link, useNavigate } from "@tanstack/react-router"
import {
  CircleNotchIcon,
  FolderIcon,
  FolderOpenIcon,
  GitPullRequestIcon,
  LightningIcon,
  MagnifyingGlassIcon,
  NotePencilIcon,
  PlusIcon,
  TrashIcon,
  PushPinIcon,
  PushPinSlashIcon,
  SparkleIcon,
} from "@phosphor-icons/react"
import { Kanban } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { SessionUser } from "@/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import type {
  SidebarProjectGroup,
  SidebarThreadItem,
} from "@/features/agents/lib/sidebarThreads"
import type { SidebarLayout } from "@/components/sidebar-layout"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { SidebarThreadRow } from "@/features/agents/components/SidebarThreadRow"
import {
  SidebarSectionAction,
  SidebarSectionHeader,
  SidebarSectionMenu,
} from "@/features/agents/components/SidebarSectionHeader"
import {
  MenuCheckboxItem,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
} from "@/components/ui/menu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  SidebarLayoutProvider,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import {
  filterThreads,
  hasActiveFilters,
} from "@/features/agents/lib/sidebarFilter"
import type {
  ChatSort,
  OrganizeMode,
  PinnedSort,
} from "@/features/agents/lib/sidebarPrefs"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  usePinAgentThread,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarProjectThreads,
  useSidebarThreads,
} from "@/features/agents/lib/queries"
import { useSidebarPullRequests } from "@/features/agents/lib/prChecks"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import {
  useDesktopLocalThreads,
  useLocalThreadActivity,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import {
  applyProjectKeyAliases,
  cloudProjectKeysByLabel,
  cloudSidebarThread,
  groupSidebarThreadsByProject,
  localSidebarThread,
  sidebarProjectOptions,
  sortSidebarThreads,
} from "@/features/agents/lib/sidebarThreads"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import { cn } from "@/lib/utils"

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

/** Threads shown per project before the group needs a "Show more". */
const PROJECT_PREVIEW_COUNT = 5

/**
 * Tracks whether the scroll container has content hidden above or below, so
 * the sidebar can show an edge hairline + fade only where there is more to
 * reach. Measured after every render because the thread list polls, and on
 * container resize.
 */
function useScrollEdges() {
  const viewport = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ top: false, bottom: false })

  const measure = useCallback(() => {
    const el = viewport.current
    if (!el) return
    const top = el.scrollTop > 0
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight > 1
    // Returning the previous object when nothing moved lets React bail out —
    // without it the dependency-free effect below would re-render forever.
    setEdges((prev) =>
      prev.top === top && prev.bottom === bottom ? prev : { top, bottom }
    )
  }, [])

  useEffect(measure)
  useEffect(() => {
    const el = viewport.current
    if (!el || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [measure])

  return { viewport, edges, measure }
}

export function AgentsSidebar({
  user,
  localOnly = false,
  activeThreadId,
  activeLocalSessionId,
  layout,
}: AgentsSidebarProps) {
  const navigate = useNavigate()
  const {
    viewport: scrollViewport,
    edges: scrollEdges,
    measure: measureScrollEdges,
  } = useScrollEdges()
  const { openPalette } = useAppCommandControls()
  const openThread = useCallback(
    (threadId: string) => {
      void navigate({ to: "/agents/$threadId", params: { threadId } })
    },
    [navigate]
  )
  const {
    prefs,
    setCompact,
    setFilters,
    toggleLocalPin,
    toggleProjectPin,
    toggleProjectCollapsed,
    toggleSectionCollapsed,
    expandProject,
    setView,
  } = useSidebarPrefs()
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
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const {
    projects: localProjects,
    addProject: addLocalProject,
    removeProject: removeLocalProject,
  } = useDesktopProjects()

  const pinnedThreads = sidebar.data.pinned ?? []
  const cloudPinnedIds = new Set(pinnedThreads.map((thread) => thread.id))
  const activeThreads = sidebar.data.active.items.filter(
    (thread) => !cloudPinnedIds.has(thread.id)
  )
  const resolvedThreads = sidebar.data.resolved.items.filter(
    (thread) => !cloudPinnedIds.has(thread.id)
  )
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
  const localPinnedIds = new Set(prefs.pinnedLocalIds)
  const localItems = localSessions
    // Removing a project has to remove its threads too, otherwise they linger
    // and re-derive the project from the cwd basename.
    .filter((thread) => projectByPath.has(thread.cwd))
    .map((thread) =>
      localSidebarThread(
        thread,
        projectByPath.get(thread.cwd),
        activity[thread.id]
      )
    )
    // Cloud threads are omitted server-side unless includeResolved; local
    // archiving is client-side, so it has to honour the same switch here.
    .filter((item) => prefs.filters.includeResolved || !item.resolved)
  const cloudItems = [
    ...pinnedThreads.map(cloudSidebarThread),
    ...activeThreads.map(cloudSidebarThread),
    ...resolvedThreads.map(cloudSidebarThread),
  ]
  // Fold a local checkout into the cloud project of the same name so the repo
  // renders as one folder; project keys are otherwise full identities.
  const aliases = cloudProjectKeysByLabel(cloudItems)
  const alignedLocalItems = applyProjectKeyAliases(localItems, aliases)
  const pinnedItems = [
    ...pinnedThreads.map(cloudSidebarThread),
    ...alignedLocalItems.filter((item) => localPinnedIds.has(item.id)),
  ]
  const threadItems: Array<SidebarThreadItem> = [
    ...activeThreads.map(cloudSidebarThread),
    ...resolvedThreads.map(cloudSidebarThread),
    ...alignedLocalItems.filter((item) => !localPinnedIds.has(item.id)),
  ]
  const allItems = [...pinnedItems, ...threadItems]
  const projects = sidebarProjectOptions(allItems, localProjects)
  const filteredPinnedItems = sortSidebarThreads(
    filterThreads(pinnedItems, prefs.filters),
    prefs.sortPinned
  )
  const filteredThreadItems = filterThreads(threadItems, prefs.filters)
  // "In one list" drops the per-project buckets and pours everything into
  // Recents, so the Projects section disappears along with its groups.
  const grouped =
    prefs.organize === "list"
      ? {
          projects: [],
          recents: sortSidebarThreads(filteredThreadItems, prefs.sortChats),
        }
      : groupSidebarThreadsByProject(
          filteredThreadItems,
          projects,
          prefs.sortChats
        )
  const pinnedProjectKeys = new Set(prefs.pinnedProjectKeys)
  const pinnedGroups = grouped.projects.filter((group) =>
    pinnedProjectKeys.has(group.key)
  )
  const unpinnedGroups = grouped.projects.filter(
    (group) => !pinnedProjectKeys.has(group.key)
  )

  const pullRequestFor = useSidebarPullRequests(allItems, !localOnly)
  const isPinned = (item: SidebarThreadItem) =>
    item.location === "cloud"
      ? cloudPinnedIds.has(item.id)
      : localPinnedIds.has(item.id)
  const isArchived = (item: SidebarThreadItem) =>
    item.location === "cloud"
      ? item.thread.resolved === true
      : item.thread.archived === true
  const toggleArchived = (item: SidebarThreadItem) => {
    if (item.location === "local") {
      void window.openSweDesktop
        ?.updateLocalThread({ threadId: item.id, archived: !isArchived(item) })
        .then(() => refreshLocalThreads(item.id))
      return
    }
    if (!resolveThread.isPending) {
      resolveThread.mutate({
        threadId: item.id,
        resolved: !isArchived(item),
      })
    }
  }
  const togglePin = (item: SidebarThreadItem) => {
    if (item.location === "local") {
      toggleLocalPin(item.id)
      return
    }
    if (!pinThread.isPending) {
      pinThread.mutate({
        threadId: item.id,
        pinned: !cloudPinnedIds.has(item.id),
      })
    }
  }

  const activeKey = activeLocalSessionId
    ? `local:${activeLocalSessionId}`
    : activeThreadId
      ? `cloud:${activeThreadId}`
      : undefined

  const rowProps = (item: SidebarThreadItem) => ({
    item,
    isActive: item.key === activeKey,
    pinned: isPinned(item),
    archived: isArchived(item),
    live: pullRequestFor(item),
    compact: prefs.compact,
    onNavigate: layout.closeOnMobile,
    onDeleteLocal: refreshLocalThreads,
    onTogglePin: () => togglePin(item),
    onToggleArchived: () => toggleArchived(item),
  })

  const sectionCollapsed = (key: string) =>
    prefs.collapsedSectionKeys.includes(key)

  // Projects and Recents share one menu: both control the same list.
  const removeProjectItems = isDesktop && localProjects.length > 0 && (
    <>
      <MenuSeparator />
      <MenuSub>
        <MenuSubTrigger>
          <TrashIcon />
          Remove project…
        </MenuSubTrigger>
        <MenuSubPopup className="w-56">
          <MenuGroup>
            {localProjects.map((project) => (
              <MenuItem
                key={project.cwd}
                onClick={() => void removeLocalProject(project.cwd)}
                variant="destructive"
              >
                <TrashIcon />
                <span className="min-w-0 truncate">{project.name}</span>
              </MenuItem>
            ))}
          </MenuGroup>
        </MenuSubPopup>
      </MenuSub>
    </>
  )

  const viewMenuItems = (
    <>
      <MenuGroup>
        <MenuGroupLabel>Organize sidebar</MenuGroupLabel>
        <MenuRadioGroup
          value={prefs.organize}
          onValueChange={(value) =>
            setView({ organize: value as OrganizeMode })
          }
        >
          <MenuRadioItem value="project">By project</MenuRadioItem>
          <MenuRadioItem value="list">In one list</MenuRadioItem>
        </MenuRadioGroup>
      </MenuGroup>
      <MenuGroup>
        <MenuGroupLabel>Sort chats by</MenuGroupLabel>
        <MenuRadioGroup
          value={prefs.sortChats}
          onValueChange={(value) => setView({ sortChats: value as ChatSort })}
        >
          <MenuRadioItem value="priority">Priority</MenuRadioItem>
          <MenuRadioItem value="updated">Last updated</MenuRadioItem>
        </MenuRadioGroup>
      </MenuGroup>
      <MenuSeparator />
      <MenuGroup>
        <MenuCheckboxItem
          checked={prefs.filters.includeResolved}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeResolved: checked })
          }
        >
          Show archived
        </MenuCheckboxItem>
        <MenuCheckboxItem
          checked={prefs.filters.includeAutomations}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeAutomations: checked })
          }
        >
          Show automations
        </MenuCheckboxItem>
        <MenuCheckboxItem checked={prefs.compact} onCheckedChange={setCompact}>
          Compact rows
        </MenuCheckboxItem>
      </MenuGroup>
    </>
  )

  // Each folder pages its own repo, so a project's "Show more" cannot spill
  // threads into the other sections.
  const repoByProjectKey = new Map(
    cloudItems.flatMap((item) =>
      item.projectKey && item.thread.repoFullName
        ? [[item.projectKey, item.thread.repoFullName] as const]
        : []
    )
  )
  const hydrateProjectThreads = (threads: Array<AgentThread>) =>
    filterThreads(
      threads
        .filter((thread) => !cloudPinnedIds.has(thread.id))
        .map(cloudSidebarThread),
      prefs.filters
    )

  const renderProjectGroup = (group: SidebarProjectGroup) => (
    <ProjectGroup
      key={group.key}
      group={group}
      activeKey={activeKey}
      collapsed={prefs.collapsedProjectKeys.includes(group.key)}
      expanded={prefs.expandedProjectKeys.includes(group.key)}
      pinned={pinnedProjectKeys.has(group.key)}
      repoFullName={
        localOnly ? null : (repoByProjectKey.get(group.key) ?? null)
      }
      includeResolved={prefs.filters.includeResolved}
      includeAutomations={prefs.filters.includeAutomations}
      sort={prefs.sortChats}
      hydrate={hydrateProjectThreads}
      onToggleCollapsed={() => toggleProjectCollapsed(group.key)}
      onExpand={() => expandProject(group.key)}
      onTogglePin={() => toggleProjectPin(group.key)}
      renderRow={(item) => (
        <SidebarThreadRow key={item.key} {...rowProps(item)} indent />
      )}
    />
  )

  const cloudPending = !localOnly && sidebar.isPending
  const resolvedLoading =
    !localOnly &&
    !sidebar.isPending &&
    prefs.filters.includeResolved &&
    sidebar.resolvedQuery.isLoading
  const sourcesLoading = cloudPending || (isDesktop && localThreads.isPending)
  const hasMoreActive = !sidebar.isPending && sidebar.data.active.hasMore
  const hasMoreArchived =
    !sidebar.isPending &&
    prefs.filters.includeResolved &&
    sidebar.data.resolved.hasMore
  // The global window is what discovers projects, so in project mode its
  // "Show more" belongs to the Projects section — under Recents it looked like
  // it was paging Recents while the threads landed in folders.
  const loadMoreThreads = (hasMoreActive || hasMoreArchived) && (
    <LoadMoreThreadsButton
      label="Load more threads"
      loading={
        sidebar.activeQuery.isFetchingNextPage ||
        sidebar.resolvedQuery.isFetchingNextPage
      }
      onClick={() => {
        if (hasMoreActive) void sidebar.activeQuery.fetchNextPage()
        if (hasMoreArchived) void sidebar.resolvedQuery.fetchNextPage()
      }}
    />
  )
  const byProject = prefs.organize === "project"
  const isEmpty =
    !cloudPending &&
    (!isDesktop || !localThreads.isPending) &&
    !resolvedLoading &&
    filteredPinnedItems.length === 0 &&
    grouped.projects.length === 0 &&
    grouped.recents.length === 0

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
          <NotePencilIcon className="size-4" />
          New Thread
        </Link>
      </div>

      <TooltipProvider delay={500} closeDelay={100}>
        <div className="relative flex min-h-0 flex-1 flex-col">
          {scrollEdges.top && (
            <>
              <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-px bg-border" />
              <div className="pointer-events-none absolute inset-x-0 top-px z-10 h-3 bg-gradient-to-b from-sidebar to-transparent" />
            </>
          )}
          {scrollEdges.bottom && (
            <>
              <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-px bg-border" />
              <div className="pointer-events-none absolute inset-x-0 bottom-px z-10 h-3 bg-gradient-to-t from-sidebar to-transparent" />
            </>
          )}
          <div
            ref={scrollViewport}
            className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
            onScroll={measureScrollEdges}
          >
            {!localOnly && (
              <nav
                className={cn(
                  "flex flex-col gap-0.5",
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

            {(filteredPinnedItems.length > 0 || pinnedGroups.length > 0) && (
              <section className="mb-3">
                <SidebarSectionHeader
                  label="Pinned"
                  collapsed={sectionCollapsed("pinned")}
                  onToggleCollapsed={() => toggleSectionCollapsed("pinned")}
                  menu={
                    <SidebarSectionMenu label="Pinned options">
                      <MenuGroup>
                        <MenuGroupLabel>Sort pinned by</MenuGroupLabel>
                        <MenuRadioGroup
                          value={prefs.sortPinned}
                          onValueChange={(value) =>
                            setView({ sortPinned: value as PinnedSort })
                          }
                        >
                          <MenuRadioItem value="priority">
                            Priority
                          </MenuRadioItem>
                          <MenuRadioItem value="updated">
                            Last updated
                          </MenuRadioItem>
                          <MenuRadioItem value="manual">
                            Manual order
                          </MenuRadioItem>
                        </MenuRadioGroup>
                      </MenuGroup>
                    </SidebarSectionMenu>
                  }
                />
                {!sectionCollapsed("pinned") && (
                  <>
                    {filteredPinnedItems.map((item) => (
                      <SidebarThreadRow key={item.key} {...rowProps(item)} />
                    ))}
                    {pinnedGroups.map(renderProjectGroup)}
                  </>
                )}
              </section>
            )}

            {byProject &&
              (unpinnedGroups.length > 0 || isDesktop || loadMoreThreads) && (
                <section className="mb-3">
                  <SidebarSectionHeader
                    label="Projects"
                    collapsed={sectionCollapsed("projects")}
                    onToggleCollapsed={() => toggleSectionCollapsed("projects")}
                    menu={
                      <SidebarSectionMenu label="Projects options">
                        {viewMenuItems}
                        {removeProjectItems}
                      </SidebarSectionMenu>
                    }
                    action={
                      isDesktop ? (
                        <SidebarSectionAction
                          label="Add project"
                          icon={<PlusIcon className="size-4" />}
                          onClick={() => void addLocalProject()}
                        />
                      ) : undefined
                    }
                  />
                  {!sectionCollapsed("projects") && (
                    <>
                      {unpinnedGroups.map(renderProjectGroup)}
                      {loadMoreThreads}
                    </>
                  )}
                </section>
              )}

            {(grouped.recents.length > 0 ||
              (!byProject && loadMoreThreads) ||
              resolvedLoading) && (
              <section className="mb-3">
                <SidebarSectionHeader
                  label="Recents"
                  collapsed={sectionCollapsed("recents")}
                  onToggleCollapsed={() => toggleSectionCollapsed("recents")}
                  menu={
                    <SidebarSectionMenu label="Recents options">
                      {viewMenuItems}
                    </SidebarSectionMenu>
                  }
                  action={
                    <SidebarSectionAction
                      label="New thread"
                      icon={<NotePencilIcon className="size-4" />}
                      onClick={() => {
                        layout.closeOnMobile()
                        void navigate({ to: "/agents" })
                      }}
                    />
                  }
                />
                {!sectionCollapsed("recents") && (
                  <>
                    {grouped.recents.map((item) => (
                      <SidebarThreadRow key={item.key} {...rowProps(item)} />
                    ))}
                    {!byProject && loadMoreThreads}
                    {resolvedLoading && (
                      <div className="flex items-center gap-1.5 px-2.5 py-2 text-xs text-muted-foreground/70">
                        <CircleNotchIcon className="size-3.5 animate-spin" />
                        Loading archived threads…
                      </div>
                    )}
                  </>
                )}
              </section>
            )}
            {isEmpty && !sidebar.isError && !localThreads.isError && (
              <p className="px-2.5 py-6 text-center text-xs text-muted-foreground/70">
                {hasActiveFilters(prefs.filters)
                  ? "No threads match these filters."
                  : "No threads yet."}
              </p>
            )}
          </div>
        </div>
      </TooltipProvider>

      <div className="p-2">
        <div className="min-w-0">
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
      </div>
    </SidebarFrame>
  )
}

function ProjectGroup({
  group,
  activeKey,
  collapsed,
  expanded,
  pinned,
  repoFullName,
  includeResolved,
  includeAutomations,
  sort,
  hydrate,
  onToggleCollapsed,
  onExpand,
  onTogglePin,
  renderRow,
}: {
  group: SidebarProjectGroup
  activeKey?: string
  collapsed: boolean
  expanded: boolean
  pinned: boolean
  repoFullName: string | null
  includeResolved: boolean
  includeAutomations: boolean
  sort: ChatSort
  hydrate: (threads: Array<AgentThread>) => Array<SidebarThreadItem>
  onToggleCollapsed: () => void
  onExpand: () => void
  onTogglePin: () => void
  renderRow: (item: SidebarThreadItem) => React.ReactNode
}) {
  const Folder = collapsed ? FolderIcon : FolderOpenIcon
  const project = useSidebarProjectThreads({
    repoFullName,
    includeResolved,
    includeAutomations,
    enabled: expanded && !collapsed,
  })
  const seeded = new Set(group.threads.map((thread) => thread.key))
  const threads = expanded
    ? sortSidebarThreads(
        [
          ...group.threads,
          ...hydrate(project.items).filter((item) => !seeded.has(item.key)),
        ],
        sort
      )
    : group.threads
  const preview = threads.slice(0, PROJECT_PREVIEW_COUNT)
  const active = threads.find((thread) => thread.key === activeKey)
  const shown = expanded
    ? threads
    : active && !preview.includes(active)
      ? [...preview.slice(0, -1), active]
      : preview
  const loading =
    project.isFetchingNextPage ||
    (expanded && Boolean(repoFullName) && project.isPending)
  // Before expanding, a full preview is the only hint that the repo has more
  // threads than the sidebar's global window happened to include.
  const hasMore = expanded
    ? project.hasMore || loading
    : shown.length < threads.length ||
      (Boolean(repoFullName) && threads.length >= PROJECT_PREVIEW_COUNT)

  return (
    <div className="mb-1">
      <div className="group/folder flex items-center gap-1.5 rounded-md pr-1 pl-2 text-[13px] text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground">
        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left"
        >
          <Folder className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{group.label}</span>
        </button>
        <button
          type="button"
          aria-label={pinned ? `Unpin ${group.label}` : `Pin ${group.label}`}
          title={pinned ? "Unpin project" : "Pin project"}
          onClick={onTogglePin}
          className="hidden size-5 shrink-0 items-center justify-center rounded text-muted-foreground/80 group-hover/folder:flex hover:bg-accent hover:text-foreground"
        >
          {pinned ? (
            <PushPinSlashIcon className="size-3.5" />
          ) : (
            <PushPinIcon className="size-3.5" />
          )}
        </button>
      </div>
      {!collapsed && (
        <>
          {shown.map(renderRow)}
          {hasMore && (
            <button
              type="button"
              onClick={() => (expanded ? project.fetchNextPage() : onExpand())}
              disabled={loading}
              className="flex w-full items-center gap-1.5 rounded-lg py-1 pr-2.5 pl-6 text-left text-[13px] text-muted-foreground/70 transition-colors hover:text-foreground disabled:cursor-wait disabled:opacity-60"
            >
              {loading && <CircleNotchIcon className="size-3.5 animate-spin" />}
              {loading ? "Loading…" : "Show more"}
            </button>
          )}
        </>
      )}
    </div>
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
  /** Screen-reader label; the button itself just reads "Show more". */
  label: string
  loading: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      aria-label={label}
      className="mt-0.5 flex w-full items-center gap-1.5 rounded-lg py-1 pr-2.5 pl-2.5 text-left text-[13px] text-muted-foreground/70 transition-colors hover:text-foreground disabled:cursor-wait disabled:opacity-60"
    >
      {loading && <CircleNotchIcon className="size-3.5 animate-spin" />}
      {loading ? "Loading…" : "Show more"}
    </button>
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
