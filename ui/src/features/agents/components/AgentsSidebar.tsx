import { Link, useNavigate, useRouterState } from "@tanstack/react-router"
import {
  CaretDownIcon,
  CaretRightIcon,
  CircleNotchIcon,
  DownloadSimpleIcon,
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
  StackIcon,
} from "@phosphor-icons/react"
import { Radar } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type { DesktopUpdateState } from "@/desktop"
import type { SessionUser } from "@/lib/api"
import type {
  PullRequestSnapshot,
  SidebarRepo,
} from "@/features/agents/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import type {
  SidebarRepoGroup,
  SidebarThreadItem,
  SidebarWorkspaceGroup,
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
  agentMutationKeys,
  agentThreadKeys,
  usePinAgentThread,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarActiveThread,
  useSidebarPinnedThreads,
  useSidebarRepos,
  useSidebarRepoThreads,
  useSidebarRecents,
  useWorkspaceOptions,
} from "@/features/agents/lib/queries"
import { useSidebarPullRequests } from "@/features/agents/lib/prChecks"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import {
  useDesktopLocalThreads,
  useLocalThreadActivity,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import {
  applyRepoKeyAliases,
  cloudSidebarThread,
  DEFAULT_SIDEBAR_WORKSPACE_SLUG,
  groupRepoGroupsByWorkspace,
  groupSidebarThreadsByRepo,
  localSidebarThread,
  sidebarRepoKey,
  sidebarRepoOptions,
  sortSidebarThreads,
} from "@/features/agents/lib/sidebarThreads"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import { cn } from "@/lib/utils"
import { useChatRoutes } from "@/lib/chatRoutes"
import {
  getLastSectionLocation,
  sectionOf,
  useHrefLinkOptions,
} from "@/lib/appLocation"
import { reportError } from "@/lib/errorReporting"
import { usePendingVariables } from "@/lib/optimistic"

interface AgentsSidebarProps {
  user: SessionUser | null
  localOnly?: boolean
  activeThreadId?: string
  activeLocalSessionId?: string
  layout: SidebarLayout
}

interface HydratedRepoGroup extends SidebarRepoGroup {
  repoFullName: string | null
  localRepoPath?: string
  updatedAt: number
  activeThread?: AgentThread
}

const NAV = [
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/agents/reviews", label: "Pull Requests", icon: GitPullRequestIcon },
  { to: "/incidents", label: "Incidents", icon: Radar },
] as const

/** Threads shown per repo before the group needs a "Show more". */
const REPO_PREVIEW_COUNT = 5
const NO_REPO_GROUP_KEY = "repo:no-repo"

function cloudRepoAliases(
  repos: ReadonlyArray<SidebarRepo>
): Map<string, string> {
  const keys = new Map<string, Array<string>>()
  for (const repo of repos) {
    const label = repo.name.trim().toLowerCase()
    const key = sidebarRepoKey(repo.repoFullName)
    if (label && key) keys.set(label, [...(keys.get(label) ?? []), key])
  }
  return new Map(
    [...keys].flatMap(([label, values]) =>
      values.length === 1 ? [[label, values[0] as string]] : []
    )
  )
}

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
  const chat = useChatRoutes()
  const {
    viewport: scrollViewport,
    edges: scrollEdges,
    measure: measureScrollEdges,
  } = useScrollEdges()
  const { openPalette } = useAppCommandControls()
  const queryClient = useQueryClient()
  const openThread = useCallback(
    (threadId: string) => {
      const review = queryClient.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )?.reviewPage
      if (review) void navigate(reviewPageRoute(review))
      else void navigate({ to: chat.thread, params: { threadId } })
    },
    [navigate, chat.thread, queryClient]
  )
  const {
    prefs,
    setCompact,
    setFilters,
    toggleLocalPin,
    toggleRepoPin,
    toggleRepoCollapsed,
    toggleSectionCollapsed,
    expandRepo,
    setView,
  } = useSidebarPrefs()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const activeSection = useRouterState({
    select: (state) => sectionOf(state.location.pathname),
  })
  const sectionLinkTarget = useHrefLinkOptions()
  const [updateState, setUpdateState] = useState<DesktopUpdateState>({
    status: "idle",
  })
  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    void desktop.getUpdateState().then(setUpdateState)
    return desktop.onUpdateState(setUpdateState)
  }, [])
  const installUpdate = useCallback(async () => {
    const desktop = window.openSweDesktop
    if (!desktop || updateState.status !== "ready") return
    const readyState = updateState
    setUpdateState({ ...readyState, status: "installing" })
    if (await desktop.installUpdate().catch(() => false)) return
    setUpdateState((current) =>
      current.status === "installing" ? readyState : current
    )
  }, [updateState])
  const updateInstalling = updateState.status === "installing"
  const workspaceOrganize = prefs.organize === "workspace"
  // "workspace" mode groups the same repo folders as "repo" mode; it
  // only changes how the unpinned ones are laid out (nested under a workspace
  // header instead of a flat list), so every other repo-mode query and
  // computation below applies to both.
  const repoMode = prefs.organize === "repo" || workspaceOrganize
  const includeAutomations =
    prefs.filters.includeAutomations ||
    prefs.filters.sources.includes("schedule")
  const pinnedQuery = useSidebarPinnedThreads({ enabled: !localOnly })
  const recentsQuery = useSidebarRecents({
    repoMode,
    includeAutomations,
    includeResolved: prefs.filters.includeResolved,
    sort: prefs.sortChats,
    enabled: !localOnly,
  })
  const sidebarReposQuery = useSidebarRepos({
    includeAutomations,
    includeResolved: prefs.filters.includeResolved,
    enabled: !localOnly && repoMode,
  })
  const workspaceOptionsQuery = useWorkspaceOptions(
    !localOnly && workspaceOrganize
  )
  // One workspace — or none yet, while the list loads — has nothing to group
  // by, so the header would add a level of nesting that says nothing. The
  // composer's workspace picker and the admin page hide themselves the same way.
  const workspaceMode =
    workspaceOrganize &&
    (workspaceOptionsQuery.data?.workspaces.length ?? 0) > 1
  const localThreads = useDesktopLocalThreads({ enabled: isDesktop })
  const localSessions = localThreads.data ?? []
  const activity = useLocalThreadActivity()
  const refreshLocalThreads = useRefreshLocalThreads()
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const pendingPins = usePendingVariables<{ threadId: string }>(
    agentMutationKeys.pin
  )
  const pendingResolves = usePendingVariables<{ threadId: string }>(
    agentMutationKeys.resolve
  )
  const {
    projects: localRepos,
    addProject: addLocalRepo,
    removeProject: removeLocalRepo,
  } = useDesktopProjects()
  const repoCommands = useMemo(
    () =>
      isDesktop
        ? [
            {
              id: "add-repository",
              label: "Add repository",
              aliases: ["open folder", "add folder", "repository", "repo"],
              group: "Workspace",
              run: async () => {
                await addLocalRepo()
              },
            },
          ]
        : [],
    [addLocalRepo, isDesktop]
  )
  useRegisterAppCommands(repoCommands)

  const pinnedThreads = pinnedQuery.data ?? []
  const cloudPinnedIds = new Set(pinnedThreads.map((thread) => thread.id))
  const pageThreads = recentsQuery.items.filter(
    (thread) => !cloudPinnedIds.has(thread.id)
  )
  const activeThread = useSidebarActiveThread({
    activeThreadId,
    loadedThreads: [...pinnedThreads, ...pageThreads],
    includeResolved: prefs.filters.includeResolved,
    enabled: !localOnly,
  })
  const activeInRepo = Boolean(repoMode && activeThread?.repoFullName.trim())
  const recentThreads = [
    ...(activeThread && !activeInRepo ? [activeThread] : []),
    ...pageThreads.filter((thread) => thread.id !== activeThread?.id),
  ]
  const visibleThreads = [...pinnedThreads, ...recentThreads]
  useSeedAgentThreadDetails(visibleThreads, activeThreadId)
  useRunCompletionNotifier(visibleThreads, activeThreadId, openThread)

  const repoByPath = new Map(localRepos.map((repo) => [repo.cwd, repo]))
  const localPinnedIds = new Set(prefs.pinnedLocalIds)
  const localItems = localSessions
    // Removing a repo has to remove its threads too, otherwise they linger
    // and re-derive the repo from the cwd basename.
    .filter((thread) => repoByPath.has(thread.cwd))
    .map((thread) =>
      localSidebarThread(
        thread,
        repoByPath.get(thread.cwd),
        activity[thread.id]
      )
    )
    // Cloud threads are omitted server-side unless includeResolved; local
    // archiving is client-side, so it has to honour the same switch here.
    .filter((item) => prefs.filters.includeResolved || !item.resolved)
  // Fold a local checkout into the cloud repo of the same name so the repo
  // renders as one folder; repo keys are otherwise full identities.
  const serverRepos = sidebarReposQuery.data ?? []
  const activeRepo: SidebarRepo | undefined = activeThread?.repoFullName.trim()
    ? {
        repoFullName: activeThread.repoFullName,
        name: activeThread.repo,
        updatedAt: activeThread.updatedAt,
        // The server repo list hasn't caught up with this thread yet;
        // it is re-grouped correctly as soon as `sidebarReposQuery` refetches.
        workspace: DEFAULT_SIDEBAR_WORKSPACE_SLUG,
      }
    : undefined
  const cloudRepos =
    activeRepo &&
    !serverRepos.some(
      (repo) =>
        repo.repoFullName.toLowerCase() ===
        activeRepo.repoFullName.toLowerCase()
    )
      ? [activeRepo, ...serverRepos]
      : serverRepos
  // A repo whose repository name is blank has no stable key, which is what
  // `sidebarRepoKey` reports with a null; it cannot be grouped or pinned.
  const keyedCloudRepos = cloudRepos.flatMap((repo) => {
    const key = sidebarRepoKey(repo.repoFullName)
    return key ? [{ repo, key }] : []
  })
  const aliases = cloudRepoAliases(cloudRepos)
  const alignedLocalItems = applyRepoKeyAliases(localItems, aliases)
  const pinnedItems = [
    ...pinnedThreads.map(cloudSidebarThread),
    ...alignedLocalItems.filter((item) => localPinnedIds.has(item.id)),
  ]
  const threadItems: Array<SidebarThreadItem> = [
    ...recentThreads.map(cloudSidebarThread),
    ...(repoMode
      ? []
      : alignedLocalItems.filter((item) => !localPinnedIds.has(item.id))),
  ]
  const allItems = [...pinnedItems, ...threadItems]
  const filteredPinnedItems = sortSidebarThreads(
    filterThreads(pinnedItems, prefs.filters),
    prefs.sortPinned
  )
  const recents = sortSidebarThreads(
    filterThreads(threadItems, prefs.filters),
    prefs.sortChats
  )
  const unpinnedLocalItems = alignedLocalItems.filter(
    (item) => !localPinnedIds.has(item.id)
  )
  const localGroups = repoMode
    ? groupSidebarThreadsByRepo(
        filterThreads(unpinnedLocalItems, prefs.filters),
        sidebarRepoOptions(unpinnedLocalItems, localRepos),
        prefs.sortChats
      ).repos
    : []
  const repoGroups: Array<HydratedRepoGroup> = repoMode
    ? [
        ...keyedCloudRepos.map(({ repo, key }) => {
          return {
            key,
            label: repo.name,
            repoFullName: repo.repoFullName,
            updatedAt: repo.updatedAt,
            activeThread:
              activeInRepo &&
              activeThread?.repoFullName.toLowerCase() ===
                repo.repoFullName.toLowerCase()
                ? activeThread
                : undefined,
            threads:
              localGroups.find((group) => group.key === key)?.threads ?? [],
          }
        }),
        ...localGroups
          .filter(
            (group) => !keyedCloudRepos.some(({ key }) => key === group.key)
          )
          .map((group) => ({
            ...group,
            repoFullName: null,
            localRepoPath: group.threads.find(
              (thread) => thread.location === "local"
            )?.thread.cwd,
            updatedAt: group.threads[0]?.updatedAt ?? 0,
          })),
      ].sort((left, right) => right.updatedAt - left.updatedAt)
    : []
  const pinnedRepoKeys = new Set(prefs.pinnedRepoKeys)
  const pinnedGroups = repoGroups.filter((group) =>
    pinnedRepoKeys.has(group.key)
  )
  const unpinnedGroups = repoGroups.filter(
    (group) => !pinnedRepoKeys.has(group.key)
  )
  // Every repository sits in exactly one workspace, so the unpinned repo
  // folders nest cleanly under workspace headers; local-only folders (no
  // server-side repo) fall under the default workspace.
  const repoWorkspaceOptions = keyedCloudRepos.map(({ repo, key }) => ({
    key,
    label: repo.name,
    workspace: repo.workspace,
  }))
  const workspaceGroups: Array<SidebarWorkspaceGroup<HydratedRepoGroup>> =
    workspaceMode
      ? groupRepoGroupsByWorkspace(
          unpinnedGroups,
          repoWorkspaceOptions,
          workspaceOptionsQuery.data?.workspaces ?? []
        )
      : []

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
        .catch((error: unknown) =>
          reportError({ title: "Couldn't archive or restore thread", error })
        )
      return
    }
    if (!pendingResolves.some((vars) => vars.threadId === item.id)) {
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
    if (!pendingPins.some((vars) => vars.threadId === item.id)) {
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

  const rowProps = (
    item: SidebarThreadItem,
    live: PullRequestSnapshot | undefined = pullRequestFor(item)
  ) => ({
    item,
    isActive: item.key === activeKey,
    pinned: isPinned(item),
    archived: isArchived(item),
    live,
    compact: prefs.compact,
    onNavigate: layout.closeOnMobile,
    onDeleteLocal: refreshLocalThreads,
    onTogglePin: () => togglePin(item),
    onToggleArchived: () => toggleArchived(item),
  })

  const sectionCollapsed = (key: string) =>
    prefs.collapsedSectionKeys.includes(key)
  const hydrateRepoThreads = (threads: Array<AgentThread>) =>
    filterThreads(
      threads
        .filter((thread) => !cloudPinnedIds.has(thread.id))
        .map(cloudSidebarThread),
      prefs.filters
    )

  // Repositories and Recents share one menu: both control the same list.
  const removeProjectItems = isDesktop && localRepos.length > 0 && (
    <>
      <MenuSeparator />
      <MenuSub>
        <MenuSubTrigger>
          <TrashIcon />
          Remove repository…
        </MenuSubTrigger>
        <MenuSubPopup className="w-56">
          <MenuGroup>
            {localRepos.map((repo) => (
              <MenuItem
                key={repo.cwd}
                onClick={() => void removeLocalRepo(repo.cwd)}
                variant="destructive"
              >
                <TrashIcon />
                <span className="min-w-0 truncate">{repo.name}</span>
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
          <MenuRadioItem value="workspace">Workspaces</MenuRadioItem>
          <MenuRadioItem value="repo">By repository</MenuRadioItem>
          <MenuRadioItem value="list">In one list</MenuRadioItem>
        </MenuRadioGroup>
      </MenuGroup>
      <MenuGroup>
        <MenuGroupLabel>Sort chats by</MenuGroupLabel>
        <MenuRadioGroup
          value={prefs.sortChats}
          onValueChange={(value) => setView({ sortChats: value as ChatSort })}
        >
          <MenuRadioItem value="created">Created</MenuRadioItem>
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

  const noRepoGroup: HydratedRepoGroup = {
    key: NO_REPO_GROUP_KEY,
    label: "No repository",
    repoFullName: null,
    updatedAt: recents[0]?.updatedAt ?? 0,
    threads: recents,
  }
  const noRepoAvailable = recents.length > 0 || recentsQuery.hasMore
  const noRepoPinned = pinnedRepoKeys.has(NO_REPO_GROUP_KEY)

  const renderRepoGroup = (group: HydratedRepoGroup) => (
    <RepoGroup
      key={group.key}
      group={group}
      activeKey={activeKey}
      collapsed={prefs.collapsedRepoKeys.includes(group.key)}
      expanded={prefs.expandedRepoKeys.includes(group.key)}
      pinned={pinnedRepoKeys.has(group.key)}
      includeResolved={prefs.filters.includeResolved}
      includeAutomations={includeAutomations}
      sort={prefs.sortChats}
      activeThreadId={activeThreadId}
      openThread={openThread}
      hydrate={hydrateRepoThreads}
      onToggleCollapsed={() => toggleRepoCollapsed(group.key)}
      onExpand={() => expandRepo(group.key)}
      onCompose={() => {
        layout.closeOnMobile()
        void navigate({
          to: group.localRepoPath ? "/agents" : chat.home,
          search: group.repoFullName
            ? { repo: group.repoFullName }
            : group.localRepoPath
              ? { localRepo: group.localRepoPath }
              : { noRepo: true },
        })
      }}
      onTogglePin={() => toggleRepoPin(group.key)}
      onLoadMore={
        group.key === NO_REPO_GROUP_KEY ? recentsQuery.fetchNextPage : undefined
      }
      hasMore={
        group.key === NO_REPO_GROUP_KEY ? recentsQuery.hasMore : undefined
      }
      loadingMore={
        group.key === NO_REPO_GROUP_KEY
          ? recentsQuery.isFetchingNextPage
          : undefined
      }
      renderRow={(item, live) => (
        <SidebarThreadRow key={item.key} {...rowProps(item, live)} indent />
      )}
    />
  )

  const cloudPending =
    !localOnly &&
    (pinnedQuery.isPending ||
      recentsQuery.isPending ||
      (repoMode && sidebarReposQuery.isPending))
  const cloudError =
    pinnedQuery.isError ||
    recentsQuery.isError ||
    (repoMode && sidebarReposQuery.isError)
  const sourcesLoading = cloudPending || (isDesktop && localThreads.isPending)
  const isEmpty =
    !cloudPending &&
    (!isDesktop || !localThreads.isPending) &&
    filteredPinnedItems.length === 0 &&
    repoGroups.length === 0 &&
    recents.length === 0

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
          <img
            src={`${import.meta.env.BASE_URL}logo-mark.png`}
            alt=""
            className="size-5"
          />
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
          to={chat.home}
          onClick={layout.closeOnMobile}
          className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-sidebar-row-hover"
        >
          <NotePencilIcon className="size-4" />
          New Thread
        </Link>
      </div>

      <TooltipProvider delay={500} closeDelay={100}>
        <div className="relative flex min-h-0 flex-1 flex-col">
          {scrollEdges.top && (
            <>
              <div className="pointer-events-none absolute inset-x-0 top-0 z-20 h-px bg-border" />
              <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-3 bg-gradient-to-b from-sidebar to-transparent" />
            </>
          )}
          {scrollEdges.bottom && (
            <>
              <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 h-px bg-border" />
              <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-3 bg-gradient-to-t from-sidebar to-transparent" />
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
                  const active = activeSection === item.to
                  return (
                    <Link
                      key={item.to}
                      {...sectionLinkTarget(
                        active ? item.to : getLastSectionLocation(item.to)
                      )}
                      onClick={layout.closeOnMobile}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-sidebar-row-hover",
                        active && "bg-sidebar-row-hover font-medium"
                      )}
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

            {(filteredPinnedItems.length > 0 ||
              pinnedGroups.length > 0 ||
              (repoMode && noRepoPinned && noRepoAvailable)) && (
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
                    {pinnedGroups.map(renderRepoGroup)}
                    {repoMode &&
                      noRepoPinned &&
                      noRepoAvailable &&
                      renderRepoGroup(noRepoGroup)}
                  </>
                )}
              </section>
            )}

            {repoMode &&
              (unpinnedGroups.length > 0 || noRepoAvailable || isDesktop) && (
                <section className="mb-3">
                  <SidebarSectionHeader
                    label={workspaceMode ? "Workspaces" : "Repositories"}
                    collapsed={sectionCollapsed("repos")}
                    onToggleCollapsed={() => toggleSectionCollapsed("repos")}
                    menu={
                      <SidebarSectionMenu label="Repositories options">
                        {viewMenuItems}
                        {removeProjectItems}
                      </SidebarSectionMenu>
                    }
                    action={
                      isDesktop ? (
                        <SidebarSectionAction
                          label="Add repository"
                          icon={<PlusIcon className="size-4" />}
                          onClick={() => void addLocalRepo()}
                        />
                      ) : undefined
                    }
                  />
                  {!sectionCollapsed("repos") && (
                    <>
                      {workspaceMode
                        ? workspaceGroups.map((workspace) => (
                            <WorkspaceGroupSection
                              key={workspace.slug}
                              workspace={workspace}
                              collapsed={sectionCollapsed(
                                `workspace:${workspace.slug}`
                              )}
                              onToggleCollapsed={() =>
                                toggleSectionCollapsed(
                                  `workspace:${workspace.slug}`
                                )
                              }
                              renderRepoGroup={renderRepoGroup}
                            />
                          ))
                        : unpinnedGroups.map(renderRepoGroup)}
                      {!noRepoPinned &&
                        noRepoAvailable &&
                        renderRepoGroup(noRepoGroup)}
                    </>
                  )}
                </section>
              )}

            {!repoMode && (
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
                        void navigate({ to: chat.home })
                      }}
                    />
                  }
                />
                {!sectionCollapsed("recents") && (
                  <>
                    {recents.map((item) => (
                      <SidebarThreadRow key={item.key} {...rowProps(item)} />
                    ))}
                    {recentsQuery.hasMore && (
                      <LoadMoreThreadsOnScroll
                        label="Load more threads"
                        root={scrollViewport}
                        loading={recentsQuery.isFetchingNextPage}
                        onLoadMore={recentsQuery.fetchNextPage}
                      />
                    )}
                  </>
                )}
              </section>
            )}
            {isEmpty && !cloudError && !localThreads.isError && (
              <p className="px-2.5 py-6 text-center text-xs text-muted-foreground/70">
                {hasActiveFilters(prefs.filters)
                  ? "No threads match these filters."
                  : "No threads yet."}
              </p>
            )}
          </div>
        </div>
      </TooltipProvider>

      <div className="flex items-center gap-2 p-2">
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
        {(updateState.status === "ready" || updateInstalling) && (
          <button
            type="button"
            title={
              updateInstalling ? "Installing update…" : "Restart to update"
            }
            aria-label={
              updateInstalling ? "Installing update" : "Restart to update"
            }
            disabled={updateInstalling}
            onClick={() => void installUpdate()}
            className="flex h-8 shrink-0 items-center justify-center gap-2 rounded-full bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
          >
            {updateInstalling ? (
              <>
                <CircleNotchIcon className="size-4 animate-spin" />
                <span>Installing…</span>
              </>
            ) : (
              <>
                <DownloadSimpleIcon className="size-4" />
                <span>Restart to update</span>
              </>
            )}
          </button>
        )}
      </div>
    </SidebarFrame>
  )
}

/**
 * A workspace header inside the "Workspaces" section, nesting the repo
 * folders that belong to it. Collapse state reuses the sidebar's generic
 * collapsed-section keys (`workspace:<slug>`), the same mechanism the
 * Pinned/Repositories/Recents headers use.
 */
function WorkspaceGroupSection({
  workspace,
  collapsed,
  onToggleCollapsed,
  renderRepoGroup,
}: {
  workspace: SidebarWorkspaceGroup<HydratedRepoGroup>
  collapsed: boolean
  onToggleCollapsed: () => void
  renderRepoGroup: (group: HydratedRepoGroup) => React.ReactNode
}) {
  const Caret = collapsed ? CaretRightIcon : CaretDownIcon
  return (
    <div className="mb-1">
      <button
        type="button"
        onClick={onToggleCollapsed}
        aria-expanded={!collapsed}
        className="group/workspace flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[13px] font-medium text-muted-foreground/70 transition-colors hover:text-foreground"
      >
        <StackIcon className="size-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
        <Caret
          className={cn(
            "size-3.5 shrink-0",
            collapsed ? "block" : "hidden group-hover/workspace:block"
          )}
        />
      </button>
      {!collapsed && (
        <div className="pl-2">{workspace.repos.map(renderRepoGroup)}</div>
      )}
    </div>
  )
}

function RepoGroup({
  group,
  activeKey,
  collapsed,
  expanded,
  pinned,
  includeResolved,
  includeAutomations,
  sort,
  activeThreadId,
  openThread,
  hydrate,
  onToggleCollapsed,
  onExpand,
  onCompose,
  onTogglePin,
  onLoadMore,
  hasMore: externalHasMore,
  loadingMore = false,
  renderRow,
}: {
  group: HydratedRepoGroup
  activeKey?: string
  collapsed: boolean
  expanded: boolean
  pinned: boolean
  includeResolved: boolean
  includeAutomations: boolean
  sort: ChatSort
  activeThreadId?: string
  openThread: (threadId: string) => void
  hydrate: (threads: Array<AgentThread>) => Array<SidebarThreadItem>
  onToggleCollapsed: () => void
  onExpand: () => void
  onCompose: () => void
  onTogglePin: () => void
  onLoadMore?: () => void
  hasMore?: boolean
  loadingMore?: boolean
  renderRow: (
    item: SidebarThreadItem,
    live: PullRequestSnapshot | undefined
  ) => React.ReactNode
}) {
  const Folder = collapsed ? FolderIcon : FolderOpenIcon
  const repo = useSidebarRepoThreads({
    repoFullName: group.repoFullName,
    includeResolved,
    includeAutomations,
    sort,
    enabled: !collapsed,
  })
  const cloudThreads = [
    ...(group.activeThread ? [group.activeThread] : []),
    ...repo.items.filter((thread) => thread.id !== group.activeThread?.id),
  ]
  useSeedAgentThreadDetails(cloudThreads, activeThreadId)
  useRunCompletionNotifier(cloudThreads, activeThreadId, openThread)
  const threads = sortSidebarThreads(
    [...hydrate(cloudThreads), ...group.threads],
    sort
  )
  const pullRequestFor = useSidebarPullRequests(
    threads,
    Boolean(group.repoFullName)
  )
  const preview = threads.slice(0, REPO_PREVIEW_COUNT)
  const active = threads.find((thread) => thread.key === activeKey)
  const shown = expanded
    ? threads
    : active && !preview.includes(active)
      ? [...preview.slice(0, -1), active]
      : preview
  const loading =
    repo.isFetchingNextPage ||
    loadingMore ||
    (Boolean(group.repoFullName) && !collapsed && repo.isPending)
  const hasMore = expanded
    ? (externalHasMore ?? repo.hasMore)
    : threads.length > REPO_PREVIEW_COUNT || (externalHasMore ?? repo.hasMore)

  return (
    <div className="mb-1">
      <div className="group/folder flex items-center gap-1.5 rounded-md pr-1 pl-2 text-sm text-foreground transition-colors hover:bg-sidebar-row-hover">
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
          title={pinned ? "Unpin repository" : "Pin repository"}
          onClick={onTogglePin}
          className="hidden size-5 shrink-0 items-center justify-center rounded text-muted-foreground/80 group-hover/folder:flex hover:bg-accent hover:text-foreground"
        >
          {pinned ? (
            <PushPinSlashIcon className="size-3.5" />
          ) : (
            <PushPinIcon className="size-3.5" />
          )}
        </button>
        <button
          type="button"
          aria-label={`Compose message in ${group.label}`}
          title="Compose message"
          onClick={onCompose}
          className="hidden size-5 shrink-0 items-center justify-center rounded text-muted-foreground/80 group-hover/folder:flex hover:bg-accent hover:text-foreground"
        >
          <NotePencilIcon className="size-3.5" />
        </button>
      </div>
      {!collapsed && (
        <>
          {shown.map((item) => renderRow(item, pullRequestFor(item)))}
          {shown.length === 0 && loading && (
            <div className="flex items-center gap-1.5 py-1 pr-2.5 pl-6 text-[13px] text-muted-foreground/70">
              <CircleNotchIcon className="size-3.5 animate-spin" />
              Loading chats…
            </div>
          )}
          {shown.length === 0 && !loading && !repo.isError && (
            <p className="py-1 pr-2.5 pl-6 text-[13px] text-muted-foreground/60">
              No chats
            </p>
          )}
          {repo.isError && (
            <button
              type="button"
              onClick={() => void repo.refetch()}
              className="w-full py-1 pr-2.5 pl-6 text-left text-[13px] text-destructive"
            >
              Retry loading chats
            </button>
          )}
          {hasMore && (
            <button
              type="button"
              onClick={() => {
                if (!expanded) onExpand()
                else if (onLoadMore) onLoadMore()
                else repo.fetchNextPage()
              }}
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
    <button
      ref={sentinel}
      type="button"
      onClick={() => load.current()}
      disabled={loading}
      aria-label={label}
      className="flex w-full items-center justify-center gap-1.5 py-2 text-[13px] text-muted-foreground/70"
    >
      {loading ? (
        <CircleNotchIcon className="size-3.5 animate-spin" />
      ) : (
        <span className="sr-only">{label}</span>
      )}
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
  // `useMutation` returns a fresh object every render; only `mutate` is stable,
  // and an unstable command array re-registers on every commit.
  const pinThread = usePinAgentThread().mutate
  const resolveThread = useResolveAgentThread().mutate
  const pinnedThreads = useSidebarPinnedThreads({
    enabled: Boolean(activeThreadId),
  })
  const activeThread = useSidebarActiveThread({
    activeThreadId,
    loadedThreads: [],
    includeResolved: true,
    enabled: Boolean(activeThreadId),
  })
  const sidebarCommands = useMemo(() => {
    const commands = [
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
    layout.toggle,
    pinThread,
    pinnedThreads.data,
    resolveThread,
  ])
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
        <main className="relative flex min-w-0 flex-1 overflow-hidden bg-background">
          {children}
        </main>
      </div>
    </SidebarLayoutProvider>
  )
}
