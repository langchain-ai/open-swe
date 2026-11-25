import { Kbd } from "@langchain/macaw-components/Kbd"
import { useShortcutLabel } from "@/lib/hotkeys"
import {
  CaretDownIcon,
  CaretRightIcon,
  MagnifyingGlassRegularIcon,
  PlusIcon,
  PushPinRegularIcon,
} from "@langchain/macaw-components/icons"
import { Link, useNavigate, useRouterState } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenuGroup,
  DropdownMenuSeparator,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@/components/Skeleton"
import { Spinner, SpinnerIcon } from "@langchain/macaw-components/Spinner"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/ssr/DownloadSimple"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderOpenIcon } from "@phosphor-icons/react/dist/ssr/FolderOpen"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import {
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { DesktopUpdateState } from "@/desktop"
import type { SessionUser } from "@/lib/api"
import { useProfile } from "@/lib/profile"
import type {
  PullRequestSnapshot,
  SidebarRepo,
} from "@/features/agents/lib/api"
import { CreateProjectDialog } from "@/features/projects/components/CreateProjectDialog"
import { ProjectFolderMenu } from "@/features/projects/components/ProjectFolderMenu"
import {
  type Project,
  threadProject,
  useProjects,
} from "@/features/projects/lib/projects"
import type { AgentThread } from "@/features/agents/lib/types"
import type {
  SidebarRepoGroup,
  SidebarThreadItem,
} from "@/features/agents/lib/sidebarThreads"
import type { SidebarLayout } from "@/components/sidebar-layout"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { SidebarNav } from "@/features/agents/components/SidebarNav"
import { SidebarGroupHeading } from "@/features/agents/components/SidebarGroupHeading"
import { ThreadDragDrop } from "@/features/agents/components/ThreadDragDrop"
import { SortableThreadList } from "@/features/agents/components/SortableThreadList"
import {
  PendingSidebarThreadRow,
  SidebarThreadHoverProvider,
  SidebarThreadRow,
} from "@/features/agents/components/SidebarThreadRow"
import { SidebarThreadSelection } from "@/features/agents/components/SidebarThreadSelection"
import {
  SidebarSectionAction,
  SidebarSectionHeader,
  SidebarSectionMenu,
} from "@/features/agents/components/SidebarSectionHeader"
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
import type { OrganizeMode } from "@/features/agents/lib/sidebarPrefs"
import {
  useSidebarPrefs,
  useSidebarPrefsHydrated,
} from "@/features/agents/lib/sidebarPrefs"
import {
  agentMutationKeys,
  agentThreadKeys,
  useActiveAgentThread,
  usePinAgentThread,
  useReorderPinnedThreads,
  useReorderSidebarThreads,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarActiveThread,
  useSidebarPinnedThreads,
  useSidebarRepos,
  useSidebarRepoThreads,
  useSidebarRecents,
} from "@/features/agents/lib/queries"
import {
  MenuCheckItem,
  MenuChoiceGroup,
} from "@/features/agents/components/MenuCheckItem"
import { useSidebarPullRequests } from "@/features/agents/lib/prChecks"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import {
  cloudSidebarThread,
  groupSidebarThreadsByRecency,
  sidebarRepoKey,
  sortSidebarThreads,
  sidebarItemContains,
  withoutNestedWorkers,
  pinnedThreadShortcuts,
} from "@/features/agents/lib/sidebarThreads"
import {
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import { cn } from "@/lib/utils"
import { usePendingVariables } from "@/lib/optimistic"
import {
  pendingThreadStarts,
  usePendingThreadStarts,
} from "@/features/agents/lib/pendingThreadStarts"
import { Icon } from "@langchain/macaw-components"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"

interface AgentsSidebarProps {
  user: SessionUser
  activeThreadId?: string
  layout: SidebarLayout
  sidebarHidden?: boolean
}

interface SidebarRowOptions {
  live?: PullRequestSnapshot
  indent?: boolean
  hidden?: boolean
  onActive?: () => void
}

interface HydratedRepoGroup extends SidebarRepoGroup {
  repoFullName: string | null
  project?: Project
  updatedAt: number
  activeThread?: AgentThread
}

const NAV_ROW_CLASS =
  "flex w-full items-center gap-space-3 rounded-md px-space-2 py-space-1 text-sm text-primary transition-colors hover:bg-sidebar-row-hover"
const GROUP_ROW_CLASS =
  "h-auto min-w-0 flex-1 justify-start gap-space-2 border-0 px-space-2 py-space-1 text-left font-medium whitespace-normal text-tertiary hover:bg-transparent hover:text-primary dark:hover:bg-transparent"
const GROUP_CARET_CLASS = "size-3.5 shrink-0"
const GROUP_HEADER_CLASS =
  "group/folder flex items-center gap-space-2 rounded-md pr-space-1 transition-colors hover:bg-sidebar-row-hover"

/** Threads shown per repo before the group needs a "Show more". */
const REPO_PREVIEW_COUNT = 5
const NO_REPO_GROUP_KEY = "repo:no-repo"

export function AgentsSidebar({
  user,
  activeThreadId,
  layout,
  sidebarHidden,
}: AgentsSidebarProps) {
  const pendingState = usePendingThreadStarts()
  const pendingStarts = pendingState.entries.filter(
    (entry) => entry.userId === user.user_id
  )
  const focusedThreadId =
    activeThreadId ??
    pendingStarts.find((entry) => entry.thread.id === pendingState.submissionId)
      ?.thread.id
  const pendingDetails = useQueries({
    queries: pendingStarts.map(({ thread }): UseQueryOptions<AgentThread> => ({
      queryKey: agentThreadKeys.detail(thread.id),
      enabled: false,
      staleTime: Infinity,
    })),
  })
  const pendingThreads = pendingStarts.map((entry, index) =>
    entry.phase === "accepted"
      ? (pendingDetails[index]?.data ?? entry.thread)
      : entry.thread
  )
  const placeholderActiveThread = pendingStarts.some(
    (entry) => entry.thread.id === activeThreadId
  )
  const pinnedSectionRef = useRef<HTMLElement>(null)
  const navigate = useNavigate()
  const historyControls = useRouterState({
    select: ({ matches }) =>
      matches.some(({ staticData }) => staticData.historyControls),
  })
  const profile = useProfile()
  const scrollViewport = useRef<HTMLDivElement>(null)
  const { openPalette } = useAppCommandControls()
  const queryClient = useQueryClient()
  const desktop =
    typeof window !== "undefined" ? window.openSweDesktop : undefined
  const approvalThreads = useQuery({
    queryKey: ["pending-local-access-threads", user.user_id],
    queryFn: () =>
      desktop?.pendingLocalAccessThreads?.() ?? Promise.resolve([]),
    enabled: Boolean(desktop?.pendingLocalAccessThreads),
  })
  useEffect(
    () =>
      desktop?.onLocalAccessRequestsChanged?.(() => {
        void queryClient.invalidateQueries({
          queryKey: ["pending-local-access-threads", user.user_id],
        })
      }),
    [desktop, queryClient, user.user_id]
  )
  const openThread = useCallback(
    (threadId: string) => {
      const review = queryClient.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )?.reviewPage
      if (review) void navigate(reviewPageRoute(review))
      else void navigate({ to: "/agents/$threadId", params: { threadId } })
    },
    [navigate, queryClient]
  )
  const prefsHydrated = useSidebarPrefsHydrated()
  const {
    prefs,
    setFilters,
    toggleRepoPin,
    toggleRepoCollapsed,
    toggleSectionCollapsed,
    expandRepo,
    setView,
  } = useSidebarPrefs()
  const isDesktop = Boolean(desktop)
  const newThreadShortcut = useShortcutLabel(
    isDesktop ? "mod+n" : "mod+shift+o"
  )
  const [updateState, setUpdateState] = useState<DesktopUpdateState>({
    status: "idle",
  })
  useEffect(() => {
    if (!desktop) return
    void desktop.getUpdateState().then(setUpdateState)
    return desktop.onUpdateState(setUpdateState)
  }, [desktop])
  const installUpdate = useCallback(async () => {
    if (!desktop || updateState.status !== "ready") return
    const readyState = updateState
    setUpdateState({ ...readyState, status: "installing" })
    if (await desktop.installUpdate().catch(() => false)) return
    setUpdateState((current) =>
      current.status === "installing" ? readyState : current
    )
  }, [desktop, updateState])
  const updateInstalling = updateState.status === "installing"
  const repoMode = prefs.organize === "repo"
  const includeAutomations =
    prefs.filters.includeAutomations ||
    prefs.filters.sources.includes("schedule")
  const pinnedQuery = useSidebarPinnedThreads({
    enabled: true,
    measure: !prefs.collapsedSectionKeys.includes("pinned"),
  })
  const recentsQuery = useSidebarRecents({
    repoMode,
    includeAutomations,
    includeResolved: prefs.filters.includeResolved,
    owned: prefs.filters.ownedOnly,
    includeSlack: prefs.filters.includeSlack,
    sort: "updated",
    enabled: true,
  })
  const sidebarReposQuery = useSidebarRepos({
    measure:
      !prefs.collapsedSectionKeys.includes("repos") ||
      (!prefs.collapsedSectionKeys.includes("pinned") &&
        prefs.pinnedRepoKeys.length > 0),
    includeAutomations,
    includeResolved: prefs.filters.includeResolved,
    owned: prefs.filters.ownedOnly,
    includeSlack: prefs.filters.includeSlack,
    enabled: repoMode,
  })
  const pinThread = usePinAgentThread()
  const reorderPins = useReorderPinnedThreads()
  const resolveThread = useResolveAgentThread()
  const pendingPins = usePendingVariables<{ threadId: string }>(
    agentMutationKeys.pin
  )
  const pendingResolves = usePendingVariables<{ threadId: string }>(
    agentMutationKeys.resolve
  )
  const { projects, isPending: projectsPending } = useProjects({
    enabled: true,
  })
  const [creatingProject, setCreatingProject] = useState(false)
  useEffect(() => {
    pendingThreadStarts.reconcile([
      ...(pinnedQuery.data ?? []),
      ...recentsQuery.items,
    ])
  }, [pendingStarts, pinnedQuery.data, recentsQuery.items])
  const startingIds = new Set(
    pendingStarts
      .filter((entry) => entry.phase !== "accepted")
      .map((entry) => entry.thread.id)
  )
  const pinnedThreads = pinnedQuery.data ?? []
  const cloudPinnedIds = new Set(pinnedThreads.map((thread) => thread.id))
  const pageThreads = pendingThreadStarts
    .merge(recentsQuery.items, pendingThreads)
    .filter((thread) => !cloudPinnedIds.has(thread.id))
  const activeThread = useSidebarActiveThread({
    activeThreadId: placeholderActiveThread ? undefined : activeThreadId,
    loadedThreads: [...pinnedThreads, ...pageThreads],
    enabled: true,
  })
  const activeProjectId =
    repoMode && activeThread
      ? (threadProject(projects, activeThread)?.id ?? null)
      : null
  const activeInRepo = Boolean(
    repoMode && (activeProjectId || activeThread?.repoFullName.trim())
  )
  const recentThreads = [
    ...pageThreads.map((thread) =>
      thread.id === activeThread?.id ? activeThread : thread
    ),
    ...(activeThread &&
    !activeInRepo &&
    !cloudPinnedIds.has(activeThread.id) &&
    !pageThreads.some((thread) => thread.id === activeThread.id)
      ? [activeThread]
      : []),
  ]
  const visibleThreads = [...pinnedThreads, ...recentThreads]
  useSeedAgentThreadDetails(
    [...pinnedThreads, ...recentsQuery.items],
    activeThreadId
  )
  useRunCompletionNotifier(visibleThreads, activeThreadId, openThread)

  const serverFolders = sidebarReposQuery.data ?? []
  const serverRepos = serverFolders.filter(
    (folder): folder is SidebarRepo => !folder.projectId
  )
  const projectActivity = new Map(
    serverFolders.flatMap((folder) =>
      folder.projectId ? [[folder.projectId, folder.updatedAt] as const] : []
    )
  )
  const cloudRepos = [
    ...serverRepos,
    ...[...recentThreads, ...(activeThread ? [activeThread] : [])].flatMap(
      (thread) =>
        thread.repoFullName.trim() && !threadProject(projects, thread)
          ? [
              {
                repoFullName: thread.repoFullName,
                name: thread.repo,
                updatedAt: thread.updatedAt,
              },
            ]
          : []
    ),
  ].filter(
    (repo, index, repos) =>
      repos.findIndex(
        (candidate) =>
          candidate.repoFullName.toLowerCase() ===
          repo.repoFullName.toLowerCase()
      ) === index
  )
  // A repo whose repository name is blank has no stable key, which is what
  // `sidebarRepoKey` reports with a null; it cannot be grouped or pinned.
  const keyedCloudRepos = cloudRepos.flatMap((repo) => {
    const key = sidebarRepoKey(repo.repoFullName)
    return key ? [{ repo, key }] : []
  })
  const pinnedItems =
    pinnedThreadShortcuts(pinnedThreads).map(cloudSidebarThread)
  const threadItems = recentThreads.map(cloudSidebarThread)
  const allItems = [...pinnedItems, ...threadItems]
  const filteredPinnedItems = filterThreads(
    pinnedItems,
    { ...prefs.filters, includeResolved: true },
    activeThreadId,
    user.user_id
  )
  const hydrateRepoThreads = (threads: Array<AgentThread>) => {
    const items = threads
      .filter((thread) => !cloudPinnedIds.has(thread.id))
      .map(cloudSidebarThread)
    return items.flatMap((item) =>
      startingIds.has(item.id)
        ? [item]
        : filterThreads([item], prefs.filters, activeThreadId, user.user_id)
    )
  }
  const recentItems = hydrateRepoThreads(recentThreads)
  const recents = repoMode ? recentItems : sortSidebarThreads(recentItems)
  const groupOf = new Map(
    recents.map((item) => {
      const projectId = threadProject(projects, item.thread)?.id
      return [
        item.id,
        projectId
          ? `project:${projectId}`
          : (item.repoKey ?? NO_REPO_GROUP_KEY),
      ]
    })
  )
  const repoGroups: Array<HydratedRepoGroup> = repoMode
    ? [
        ...projects.map((project) => ({
          key: `project:${project.id}`,
          label: project.name,
          repoFullName: project.repo_full_name,
          project,
          updatedAt:
            projectActivity.get(project.id) ?? Date.parse(project.updated_at),
          activeThread:
            activeProjectId === project.id ? activeThread : undefined,
          threads: recents.filter(
            (item) => groupOf.get(item.id) === `project:${project.id}`
          ),
        })),
        ...keyedCloudRepos.map(({ repo, key }) => {
          return {
            key,
            label: repo.name,
            repoFullName: repo.repoFullName,
            updatedAt: repo.updatedAt,
            activeThread:
              activeInRepo &&
              !activeProjectId &&
              activeThread?.repoFullName.toLowerCase() ===
                repo.repoFullName.toLowerCase()
                ? activeThread
                : undefined,
            threads: recents.filter((item) => groupOf.get(item.id) === key),
          }
        }),
      ].sort((left, right) => right.updatedAt - left.updatedAt)
    : []
  const pinnedRepoKeys = new Set(prefs.pinnedRepoKeys)
  const pinnedGroups = repoGroups.filter((group) =>
    pinnedRepoKeys.has(group.key)
  )
  const unpinnedGroups = repoGroups.filter(
    (group) => !pinnedRepoKeys.has(group.key)
  )
  const pullRequestFor = useSidebarPullRequests(allItems, true)
  const isArchived = (item: SidebarThreadItem) => item.thread.resolved === true
  const toggleArchived = (item: SidebarThreadItem) => {
    if (!pendingResolves.some((vars) => vars.threadId === item.id)) {
      resolveThread.mutate({
        threadId: item.id,
        resolved: !isArchived(item),
      })
    }
  }
  const togglePin = (item: SidebarThreadItem) => {
    if (!pendingPins.some((vars) => vars.threadId === item.id)) {
      pinThread.mutate({
        threadId: item.id,
        pinned: !cloudPinnedIds.has(item.id),
      })
    }
  }

  const activeKey = focusedThreadId ? `cloud:${focusedThreadId}` : undefined

  const rowProps = (
    item: SidebarThreadItem,
    live: PullRequestSnapshot | undefined = pullRequestFor(item)
  ) => ({
    item,
    isActive: item.key === activeKey,
    activeThreadId,
    pinned: cloudPinnedIds.has(item.id),
    archived: isArchived(item),
    live,
    onNavigate: layout.closeOnMobile,
    onTogglePin: () => togglePin(item),
    onToggleArchived: () => toggleArchived(item),
  })

  const sectionCollapsed = (key: string) =>
    prefs.collapsedSectionKeys.includes(key)
  const viewMenuItems = (
    <>
      <MenuChoiceGroup<OrganizeMode>
        label="Group by"
        value={prefs.organize}
        onChange={(organize) => setView({ organize })}
        options={[
          { value: "date", label: "Date" },
          { value: "repo", label: "Project" },
        ]}
      />
      <DropdownMenuSeparator />
      <DropdownMenuGroup>
        <MenuCheckItem
          checked={prefs.filters.includeResolved}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeResolved: checked })
          }
        >
          Show archived
        </MenuCheckItem>
        <MenuCheckItem
          checked={prefs.filters.includeAutomations}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeAutomations: checked })
          }
        >
          Show automations
        </MenuCheckItem>
        <MenuCheckItem
          checked={prefs.filters.includeSlack}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeSlack: checked })
          }
        >
          Show Slack
        </MenuCheckItem>
        <MenuCheckItem
          checked={prefs.filters.ownedOnly}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, ownedOnly: checked })
          }
        >
          Only my threads
        </MenuCheckItem>
      </DropdownMenuGroup>
    </>
  )

  const threadGroups = (() => {
    if (prefs.organize === "date") return groupSidebarThreadsByRecency(recents)
    return []
  })()
  const recentGroups = threadGroups.length
    ? threadGroups.map((group) => ({ ...group, key: `recents:${group.key}` }))
    : [
        {
          key: "recents",
          label: "Recents",
          threads: repoMode
            ? sortSidebarThreads(
                recents.filter(
                  (item) => groupOf.get(item.id) === NO_REPO_GROUP_KEY
                )
              )
            : recents,
        },
      ]

  const renderRow = (
    item: SidebarThreadItem,
    { live, indent = false, hidden = false, onActive }: SidebarRowOptions = {}
  ) => {
    const pending = pendingStarts.find((entry) => entry.thread.id === item.id)
    return pending && pending.phase !== "accepted" ? (
      <PendingSidebarThreadRow
        key={item.key}
        item={item}
        isActive={item.key === activeKey}
        stopRequested={pending.phase === "stopping"}
        indent={indent}
        hidden={hidden}
        onActive={onActive}
        onNavigate={layout.closeOnMobile}
      />
    ) : (
      <SidebarThreadRow
        key={item.key}
        {...rowProps(item, live)}
        needsApproval={Boolean(
          desktop?.pendingLocalAccessThreads &&
          approvalThreads.data?.includes(item.id)
        )}
        indent={indent}
        hidden={hidden}
        onActive={onActive}
      />
    )
  }

  const renderRepoGroup = (
    group: HydratedRepoGroup,
    onActive: () => void,
    hidden: boolean
  ) => (
    <RepoGroup
      key={group.key}
      group={group}
      ownerlessIds={pendingThreadStarts.reorderableIds(
        recentsQuery.items.filter((thread) => !cloudPinnedIds.has(thread.id))
      )}
      listed={Boolean(
        group.project ||
        serverRepos.some(
          (repo) => sidebarRepoKey(repo.repoFullName) === group.key
        )
      )}
      activeKey={activeKey}
      hidden={hidden}
      onActive={onActive}
      collapsed={prefs.collapsedRepoKeys.includes(group.key)}
      expanded={prefs.expandedRepoKeys.includes(group.key)}
      pinned={pinnedRepoKeys.has(group.key)}
      includeResolved={prefs.filters.includeResolved}
      includeAutomations={includeAutomations}
      owned={prefs.filters.ownedOnly}
      includeSlack={prefs.filters.includeSlack}
      activeThreadId={activeThreadId}
      openThread={openThread}
      hydrate={hydrateRepoThreads}
      onToggleCollapsed={() => toggleRepoCollapsed(group.key)}
      onExpand={() => expandRepo(group.key)}
      composeTo="/agents"
      onCompose={layout.closeOnMobile}
      onTogglePin={() => toggleRepoPin(group.key)}
      renderRow={(item, options) =>
        renderRow(item, { ...options, indent: true })
      }
    />
  )

  const foldersPending =
    repoMode && (sidebarReposQuery.isPending || projectsPending)
  const pinnedPending =
    pinnedQuery.isPending || (foldersPending && prefs.pinnedRepoKeys.length > 0)
  const cloudPending =
    pinnedQuery.isPending || recentsQuery.isPending || foldersPending
  const cloudError =
    pinnedQuery.isError ||
    recentsQuery.isError ||
    (repoMode && sidebarReposQuery.isError)
  const isEmpty =
    pendingStarts.length === 0 &&
    !cloudPending &&
    filteredPinnedItems.length === 0 &&
    repoGroups.length === 0 &&
    recents.length === 0

  const recentSections = (() => {
    if (
      repoMode &&
      !recentsQuery.isPending &&
      !recentsQuery.hasMore &&
      recentGroups.every((group) => group.threads.length === 0)
    )
      return []
    const groups =
      recentsQuery.isPending && recents.length === 0
        ? [{ key: "recents", label: "Recents", threads: [] }]
        : recentGroups
    return groups.map((group, index) => ({
      key: group.key,
      content: (
        <section
          key={group.key}
          className="mb-space-3"
          data-sidebar-loading={
            !sectionCollapsed(group.key) && recentsQuery.isPending
              ? ""
              : undefined
          }
        >
          <SidebarSectionHeader
            label={group.label}
            collapsed={sectionCollapsed(group.key)}
            onToggleCollapsed={() => toggleSectionCollapsed(group.key)}
            menu={
              index === 0 && (
                <SidebarSectionMenu label="Recents options">
                  {viewMenuItems}
                </SidebarSectionMenu>
              )
            }
            action={
              index === 0 && (
                <SidebarSectionAction
                  label="New thread"
                  icon={PlusIcon}
                  onClick={() => {
                    layout.closeOnMobile()
                    void navigate({
                      to: "/agents",
                      search: repoMode ? { noRepo: true } : {},
                    })
                  }}
                />
              )
            }
          />

          <div hidden={sectionCollapsed(group.key)}>
            {group.threads.map((item) =>
              renderRow(item, {
                hidden: sectionCollapsed(group.key),
                onActive: () => {
                  if (sectionCollapsed(group.key))
                    toggleSectionCollapsed(group.key)
                },
              })
            )}
            {!sectionCollapsed(group.key) &&
              index === 0 &&
              recentsQuery.isPending && (
                <ThreadListSkeleton label="Loading recent threads" />
              )}
          </div>
        </section>
      ),
    }))
  })()

  return (
    <SidebarFrame
      {...layout}
      historyControls={historyControls}
      hidden={sidebarHidden}
      className="border-r border-muted bg-surface-level-2"
    >
      {creatingProject && (
        <CreateProjectDialog open onOpenChange={setCreatingProject} />
      )}
      <div
        className={cn(
          "flex items-center justify-between px-space-4 pb-space-4",
          isDesktop ? "pt-13" : "pt-space-4"
        )}
      >
        <Link
          to="/my-settings"
          className="min-w-0 truncate font-heading text-lg font-semibold tracking-tight text-primary"
        >
          Robin
        </Link>
        <div className="flex shrink-0 items-center gap-space-1">
          <IconButton
            icon={MagnifyingGlassRegularIcon}
            label="Search"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={() => {
              layout.closeOnMobile()
              openPalette()
            }}
          />
          <SidebarCollapseButton onToggle={layout.toggle} />
        </div>
      </div>

      <div className="shrink-0 px-space-2 pb-space-1">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className={cn(NAV_ROW_CLASS, "group")}
        >
          <PlusIcon size={20} className="-mx-0.5" />
          New
          <Kbd
            aria-hidden
            className="ml-auto opacity-0 transition-opacity group-hover:opacity-100"
          >
            {newThreadShortcut}
          </Kbd>
        </Link>
      </div>

      <SidebarThreadSelection>
        <ThreadDragDrop
          pinnedSectionRef={pinnedSectionRef}
          onAction={(actions) => {
            const validActions = actions.filter((item) => {
              const isThread = (vars: { threadId: string }) =>
                vars.threadId === item.threadId

              if (item.action === "pin" || item.action === "unpin") {
                return !pendingPins.some(isThread)
              } else if (
                item.action === "archive" ||
                item.action === "unarchive"
              ) {
                return !pendingResolves.some(isThread)
              }

              return false
            })

            for (const { threadId, action } of validActions) {
              if (action === "pin" || action === "unpin") {
                const pinned = action === "pin"
                pinThread.mutate({ threadId, pinned })
              } else {
                const resolved = action === "archive"
                resolveThread.mutate({ threadId, resolved })
              }
            }
          }}
        >
          <div
            ref={scrollViewport}
            className={cn(
              "scroll-mask-t scroll-mask-b min-h-0 flex-1 overflow-y-auto px-space-2 pb-space-2",
              pendingStarts.length === 0 && "sidebar-content",
              user && profile.isPending && "invisible"
            )}
          >
            <SidebarNav
              activeThreadId={activeThreadId}
              containerClassName="px-0"
              className={cn(
                isDesktop ? "pb-space-3" : "pb-space-4",
                !prefsHydrated && "invisible"
              )}
              onNavigate={layout.closeOnMobile}
            />
            {(pinnedPending ||
              filteredPinnedItems.length > 0 ||
              pinnedGroups.length > 0) && (
              <section
                ref={pinnedSectionRef}
                className="mb-space-3"
                data-sidebar-loading={pinnedPending ? "" : undefined}
              >
                <SidebarSectionHeader
                  label="Pinned"
                  collapsed={sectionCollapsed("pinned")}
                  onToggleCollapsed={() => toggleSectionCollapsed("pinned")}
                />
                <div hidden={sectionCollapsed("pinned")}>
                  <SortableThreadList
                    label="pinned threads"
                    items={
                      pinnedPending && pendingStarts.length === 0
                        ? []
                        : filteredPinnedItems
                    }
                    threadIds={pendingThreadStarts.reorderableIds(
                      pinnedThreads
                    )}
                    disabled={reorderPins.isPending || pendingPins.length > 0}
                    onReorder={(ids) => reorderPins.mutate(ids)}
                    renderRow={(item) =>
                      renderRow(item, {
                        hidden: sectionCollapsed("pinned"),
                        onActive: () => {
                          if (sectionCollapsed("pinned"))
                            toggleSectionCollapsed("pinned")
                        },
                      })
                    }
                  />
                  {!sectionCollapsed("pinned") && pinnedPending && (
                    <ThreadListSkeleton label="Loading pinned threads" />
                  )}
                  {(!pinnedPending || pendingStarts.length > 0) && (
                    <div className="sidebar-groups">
                      {pinnedGroups.map((group) =>
                        renderRepoGroup(
                          group,
                          () => {
                            if (sectionCollapsed("pinned"))
                              toggleSectionCollapsed("pinned")
                          },
                          sectionCollapsed("pinned")
                        )
                      )}
                    </div>
                  )}
                </div>
              </section>
            )}
            {cloudError && (
              <ThreadSourceError
                label="Cloud threads unavailable"
                onRetry={() => {
                  void pinnedQuery.refetch()
                  void recentsQuery.refetch()
                  if (repoMode) void sidebarReposQuery.refetch()
                }}
              />
            )}

            {repoMode && (
              <section
                className="mb-space-3"
                data-sidebar-loading={foldersPending ? "" : undefined}
              >
                <SidebarSectionHeader
                  label="Projects"
                  collapsed={sectionCollapsed("repos")}
                  onToggleCollapsed={() => toggleSectionCollapsed("repos")}
                  menu={
                    <SidebarSectionMenu label="Projects options">
                      {viewMenuItems}
                    </SidebarSectionMenu>
                  }
                  action={
                    <SidebarSectionAction
                      label="New project"
                      icon={PlusIcon}
                      onClick={() => setCreatingProject(true)}
                    />
                  }
                />
                <div hidden={sectionCollapsed("repos")}>
                  {!sectionCollapsed("repos") && foldersPending && (
                    <ThreadListSkeleton label="Loading projects" />
                  )}
                  {(!foldersPending || pendingStarts.length > 0) && (
                    <div className="sidebar-groups">
                      {unpinnedGroups.map((group) =>
                        renderRepoGroup(
                          group,
                          () => {
                            if (sectionCollapsed("repos"))
                              toggleSectionCollapsed("repos")
                          },
                          sectionCollapsed("repos")
                        )
                      )}
                    </div>
                  )}
                </div>
              </section>
            )}

            {recentSections.map((section) => section.content)}
            {(!repoMode || !sectionCollapsed("recents")) &&
              recentsQuery.hasMore && (
                <LoadMoreThreadsOnScroll
                  label="Load more threads"
                  root={scrollViewport}
                  loading={recentsQuery.isFetchingNextPage}
                  onLoadMore={recentsQuery.fetchNextPage}
                />
              )}
            {isEmpty && !cloudError && (
              <p className="px-space-2 py-space-5 text-center text-xs text-tertiary">
                {hasActiveFilters(prefs.filters)
                  ? "No threads match these filters."
                  : "No threads yet."}
              </p>
            )}
          </div>
        </ThreadDragDrop>
      </SidebarThreadSelection>
      <div className="flex items-center gap-space-2 p-space-2">
        <div className="min-w-0 flex-1">
          {user ? (
            <SidebarUserMenu user={user} showSettingsLink />
          ) : (
            <Button
              as={<Link to="/login" />}
              color="secondary"
              variant="outlined"
              size="md"
              className="w-full"
            >
              Sign in for cloud mode
            </Button>
          )}
        </div>
        {(updateState.status === "ready" || updateInstalling) && (
          <Button
            color="primary"
            size="md"
            title={
              updateInstalling ? "Installing update…" : "Restart to update"
            }
            aria-label={
              updateInstalling ? "Installing update" : "Restart to update"
            }
            disabled={updateInstalling}
            onClick={() => void installUpdate()}
            leftDecorator={updateInstalling ? SpinnerIcon : DownloadSimpleIcon}
            className="shrink-0 rounded-full"
          >
            {updateInstalling ? "Installing…" : "Restart to update"}
          </Button>
        )}
      </div>
    </SidebarFrame>
  )
}

function RepoGroup({
  group,
  listed,
  ownerlessIds,
  activeKey,
  hidden,
  onActive,
  collapsed,
  expanded,
  pinned,
  includeResolved,
  includeAutomations,
  owned,
  includeSlack,
  activeThreadId,
  openThread,
  hydrate,
  onToggleCollapsed,
  onExpand,
  composeTo,
  onCompose,
  onTogglePin,
  renderRow,
}: {
  group: HydratedRepoGroup
  listed: boolean
  ownerlessIds: Array<string>
  activeKey?: string
  hidden: boolean
  onActive: () => void
  collapsed: boolean
  expanded: boolean
  pinned: boolean
  includeResolved: boolean
  includeAutomations: boolean
  owned: boolean
  includeSlack: boolean
  activeThreadId?: string
  openThread: (threadId: string) => void
  hydrate: (threads: Array<AgentThread>) => Array<SidebarThreadItem>
  onToggleCollapsed: () => void
  onExpand: () => void
  composeTo: "/agents"
  onCompose: () => void
  onTogglePin: () => void
  renderRow: (
    item: SidebarThreadItem,
    options: SidebarRowOptions
  ) => React.ReactNode
}) {
  const Folder = collapsed ? FolderIcon : FolderOpenIcon
  const Caret = collapsed ? CaretRightIcon : CaretDownIcon
  const repo = useSidebarRepoThreads({
    repoFullName: group.repoFullName,
    projectId: group.project?.id ?? null,
    includeResolved,
    includeAutomations,
    owned,
    includeSlack,
    sort: "created",
    enabled:
      !hidden && !collapsed && Boolean(group.project || group.repoFullName),
  })
  useEffect(() => {
    if (listed) pendingThreadStarts.reconcile(repo.items)
  }, [listed, group.threads, repo.items])
  const cloudThreads = withoutNestedWorkers([
    ...(group.project || group.repoFullName ? repo.items : []).map((thread) =>
      thread.id === group.activeThread?.id ? group.activeThread : thread
    ),
    ...(group.activeThread &&
    !repo.items.some((thread) => thread.id === group.activeThread?.id)
      ? [group.activeThread]
      : []),
  ])
  useSeedAgentThreadDetails(cloudThreads, activeThreadId)
  useRunCompletionNotifier(cloudThreads, activeThreadId, openThread)
  const threads = hydrate(
    withoutNestedWorkers(
      pendingThreadStarts.merge(
        cloudThreads,
        group.threads.map((item) => item.thread)
      )
    )
  )
  const reorderChats = useReorderSidebarThreads(
    group.project
      ? { project: group.project.id }
      : group.repoFullName
        ? { repo: group.repoFullName }
        : { ownerless: true }
  )
  const pullRequestFor = useSidebarPullRequests(
    threads,
    Boolean(group.repoFullName)
  )
  const preview = threads.slice(0, REPO_PREVIEW_COUNT)
  const active = threads.find((thread) =>
    sidebarItemContains(thread, activeKey)
  )
  const shown = expanded
    ? threads
    : active && !preview.includes(active)
      ? [...preview.slice(0, -1), active]
      : preview
  const pending = Boolean(group.project || group.repoFullName) && repo.isPending
  const loading = pending || repo.isFetchingNextPage
  const hasMore = expanded
    ? repo.hasMore
    : threads.length > REPO_PREVIEW_COUNT || repo.hasMore

  return (
    <div
      className="sidebar-group mb-space-1"
      data-sidebar-loading={!hidden && !collapsed && pending ? "" : undefined}
    >
      <div className={GROUP_HEADER_CLASS}>
        <Button
          color="secondary"
          variant="plain"
          size="sm"
          aria-label={group.label}
          type="button"
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          className={GROUP_ROW_CLASS}
        >
          <Folder size={16} weight="regular" className="shrink-0" />
          <span className="flex min-w-0 items-center gap-space-1">
            <SidebarGroupHeading label={group.label} />
            <Caret
              weight="regular"
              className={cn(
                GROUP_CARET_CLASS,
                !collapsed &&
                  "invisible group-focus-within/folder:visible group-hover/folder:visible"
              )}
            />
          </span>
        </Button>
        {group.project && <ProjectFolderMenu project={group.project} />}
        <IconButton
          icon={pinned ? PushPinSlashIcon : PushPinRegularIcon}
          label={pinned ? `Unpin ${group.label}` : `Pin ${group.label}`}
          tooltipProps={{
            title: pinned ? "Unpin repository" : "Pin repository",
          }}
          size="xs"
          color="secondary"
          variant="plain"
          onClick={onTogglePin}
          className="hidden group-hover/folder:inline-flex"
        />
        <IconButton
          icon={PlusIcon}
          label={`Compose message in ${group.label}`}
          tooltipProps={{ title: "Compose message" }}
          size="xs"
          color="secondary"
          variant="plain"
          asChild
          className="hidden group-hover/folder:inline-flex"
        >
          <Link
            to={composeTo}
            search={
              group.project
                ? { project: group.project.id }
                : group.repoFullName
                  ? { repo: group.repoFullName }
                  : { noRepo: true }
            }
            onClick={onCompose}
          />
        </IconButton>
      </div>
      <div hidden={collapsed}>
        <SortableThreadList
          label={`chats in ${group.label}`}
          items={shown}
          threadIds={
            group.project || group.repoFullName
              ? pendingThreadStarts.reorderableIds(repo.items)
              : ownerlessIds
          }
          disabled={reorderChats.isPending}
          onReorder={(ids) => reorderChats.mutate(ids)}
          renderRow={(item) =>
            renderRow(item, {
              live: pullRequestFor(item),
              hidden: hidden || collapsed,
              onActive: () => {
                if (collapsed) onToggleCollapsed()
                onActive()
              },
            })
          }
        />
        {!hidden && !collapsed && pending && (
          <div className="pl-space-5">
            <ThreadListSkeleton label={`Loading chats in ${group.label}`} />
          </div>
        )}
        {shown.length === 0 && !loading && !repo.isError ? (
          <p className="py-space-1 pr-space-2 pl-space-6 text-xs text-tertiary">
            No chats
          </p>
        ) : null}
        {repo.isError && (
          <Button
            color="secondary"
            variant="plain"
            size="sm"
            aria-label="Retry loading chats"
            type="button"
            onClick={() => void repo.refetch()}
            className="h-6 w-full justify-start py-space-1 pr-space-2 pl-space-6 text-left"
          >
            <Icon
              icon={ArrowCounterClockwiseIcon}
              className="text-error-secondary"
            />
            <span className="text-sm text-error-secondary">
              Retry loading chats
            </span>
          </Button>
        )}
        {hasMore && !pending && (
          <Button
            color="secondary"
            variant="plain"
            size="sm"
            aria-label={loading ? "Loading…" : "Show more"}
            type="button"
            onClick={() => {
              if (!expanded) onExpand()
              else repo.fetchNextPage()
            }}
            disabled={loading}
            className="h-6 w-full justify-start gap-space-2 rounded-lg border-none py-space-1 pr-space-2 pl-space-6 text-left whitespace-normal hover:text-primary disabled:cursor-wait disabled:bg-transparent disabled:text-primary disabled:opacity-60"
          >
            {loading && <Spinner size="xxs" />}
            <span className="text-sm text-quaternary">
              {loading ? "Loading…" : "Show more..."}
            </span>
          </Button>
        )}
      </div>
    </div>
  )
}

function ThreadListSkeleton({ label }: { label: string }) {
  return (
    <div
      data-testid="sidebar-threads-skeleton"
      data-sidebar-loading
      role="status"
      aria-label={label}
    >
      {[90, 64, 76].map((width) => (
        <div
          key={width}
          className="mb-0.5 flex h-8 items-center gap-space-2 px-space-2"
          aria-hidden
        >
          <Skeleton className="size-3 shrink-0 rounded-full" />
          <Skeleton className="h-2.5" style={{ width: `${width}%` }} />
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
    <div className="flex items-center gap-space-2 px-space-2 py-space-2 text-xs text-secondary">
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <Button
        size="xs"
        color="secondary"
        variant="plain"
        className="shrink-0"
        onClick={onRetry}
      >
        Retry
      </Button>
    </div>
  )
}

function LoadMoreThreadsOnScroll({
  label,
  root,
  loading,
  onLoadMore,
}: {
  label: string
  root: React.RefObject<HTMLDivElement | null>
  loading: boolean
  onLoadMore: () => void
}) {
  const sentinel = useRef<HTMLButtonElement>(null)
  const load = useRef(onLoadMore)
  useEffect(() => {
    load.current = onLoadMore
  })
  useEffect(() => {
    const node = sentinel.current
    if (!node || typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver(
      (entries) => {
        if (!loading && entries.some((entry) => entry.isIntersecting)) {
          load.current()
        }
      },
      { root: root.current, rootMargin: "200px" }
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [loading, root])

  return (
    <Button
      color="secondary"
      variant="plain"
      size="sm"
      ref={sentinel}
      type="button"
      onClick={() => load.current()}
      disabled={loading}
      aria-label={label}
      className="h-auto w-full justify-center gap-space-2 py-space-2 whitespace-normal text-tertiary disabled:bg-transparent"
    >
      {loading ? (
        <Spinner size="xxs" />
      ) : (
        <span className="sr-only">{label}</span>
      )}
    </Button>
  )
}

export function AgentsShell({
  user,
  activeThreadId,
  hideThreadSidebar = false,
  children,
}: {
  user: SessionUser
  activeThreadId?: string
  hideThreadSidebar?: boolean
  children: React.ReactNode
}) {
  const layout = useSidebarLayout()
  const startingActiveThread = usePendingThreadStarts().entries.some(
    (entry) =>
      entry.thread.id === activeThreadId &&
      entry.userId === user.user_id &&
      entry.phase !== "accepted"
  )
  // `useMutation` returns a fresh object every render; only `mutate` is stable,
  // and an unstable command array re-registers on every commit.
  const pinThread = usePinAgentThread().mutate
  const resolveThread = useResolveAgentThread().mutate
  const pinnedThreads = useSidebarPinnedThreads({
    enabled: Boolean(activeThreadId),
  })
  const activeThread = useActiveAgentThread(
    startingActiveThread ? undefined : activeThreadId
  )
  const sidebarCommands = useMemo(() => {
    const commands = hideThreadSidebar
      ? []
      : [
          {
            id: "toggle-sidebar",
            label: "Toggle sidebar",
            aliases: ["show sidebar", "hide sidebar"],
            shortcuts: ["mod+b"],
            group: "Workspace",
            run: layout.toggle,
            desktopId: "toggle-sidebar" as const,
            desktopShortcuts: ["mod+b"],
            enableInFormFields: true,
          },
        ]
    if (!activeThread) return commands
    const reference =
      activeThread.pullRequests?.at(-1)?.url ??
      activeThread.pr?.url ??
      activeThread.id
    return [
      ...commands,
      {
        id: "copy-thread-reference",
        label:
          reference === activeThread.id ? "Copy thread ID" : "Copy PR link",
        aliases: ["copy reference", "pull request", "pr link"],
        shortcuts: ["mod+shift+c"],
        group: "Thread",
        run: () => navigator.clipboard.writeText(reference),
      },
      {
        id: "pin-thread",
        label: pinnedThreads.data?.some(
          (thread) => thread.id === activeThread.id
        )
          ? "Unpin thread"
          : "Pin thread",
        aliases: ["pin thread", "unpin thread"],
        shortcuts: ["mod+shift+p"],
        group: "Thread",
        run: () =>
          pinThread({
            threadId: activeThread.id,
            pinned: !pinnedThreads.data?.some(
              (thread) => thread.id === activeThread.id
            ),
          }),
      },
      {
        id: "archive-thread",
        label: activeThread.resolved ? "Unarchive thread" : "Archive thread",
        aliases: ["resolve thread", "settle thread", "restore thread"],
        shortcuts: ["mod+shift+s"],
        group: "Thread",
        run: () =>
          resolveThread({
            threadId: activeThread.id,
            resolved: !activeThread.resolved,
          }),
      },
    ]
  }, [
    activeThread,
    hideThreadSidebar,
    layout.toggle,
    pinThread,
    pinnedThreads.data,
    resolveThread,
  ])
  useRegisterAppCommands(sidebarCommands)

  return (
    <SidebarLayoutProvider value={layout}>
      <TooltipProvider delayDuration={500} skipDelayDuration={100}>
        <SidebarThreadHoverProvider>
          <div className="agents-ui flex h-svh overflow-hidden bg-surface-level-1">
            <AgentsSidebar
              user={user}
              activeThreadId={activeThreadId}
              layout={layout}
              sidebarHidden={hideThreadSidebar}
            />
            <main className="relative flex min-w-0 flex-1 overflow-hidden bg-surface-level-1">
              {children}
            </main>
          </div>
        </SidebarThreadHoverProvider>
      </TooltipProvider>
    </SidebarLayoutProvider>
  )
}
