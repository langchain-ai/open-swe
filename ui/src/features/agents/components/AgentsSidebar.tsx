import { useNavigate, useRouterState } from "@tanstack/react-router"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { ReactNode, RefObject } from "react"
import { AppShell } from "@langchain/gtm-platform-design-system/patterns/app-shell"
import {
  SidebarNav,
  SidebarNavItem,
} from "@langchain/gtm-platform-design-system/patterns/sidebar-nav"
import { SidebarTreeGroup } from "@langchain/gtm-platform-design-system/patterns/sidebar-tree"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import type { Glyph } from "@/components/glyphs"
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
import type {
  ChatSort,
  OrganizeMode,
  PinnedSort,
} from "@/features/agents/lib/sidebarPrefs"
import {
  AlertTriangle,
  Bot,
  Download,
  FileEdit,
  Filter,
  GitPullRequest,
  MessageCircle,
  Plus,
  PushPin,
  PinOff,
  Search,
  Sparkles,
  Stack as StackGlyph,
  Trash2,
  Zap,
} from "@/components/glyphs"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { AppRailBrand } from "@/components/rail/AppRailBrand"
import { useRailCollapsed } from "@/components/rail/useRailCollapsed"
import {
  SidebarThreadDot,
  SidebarThreadRow,
} from "@/features/agents/components/SidebarThreadRow"
import {
  REVEAL_ON_HEADING_CLASS,
  SidebarSection,
  SidebarSectionAction,
  SidebarSectionMenu,
} from "@/features/agents/components/SidebarSectionHeader"
import {
  filterThreads,
  hasActiveFilters,
} from "@/features/agents/lib/sidebarFilter"
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
  useLegacyLocalThreads,
  useLegacyLocalActivity,
  useRefreshLegacyLocalThreads,
} from "@/features/agents/lib/legacyLocal"
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
import { api } from "@/lib/api"
import {
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import type { SectionRoot } from "@/lib/appLocation"
import { getLastSectionLocation, sectionOf } from "@/lib/appLocation"
import { useChatRoutes } from "@/lib/chatRoutes"
import { reportError } from "@/lib/errorReporting"
import { usePendingVariables } from "@/lib/optimistic"
import { useProfile } from "@/lib/profile"

interface HydratedRepoGroup extends SidebarRepoGroup {
  repoFullName: string | null
  localRepoPath?: string
  updatedAt: number
  activeThread?: AgentThread
}

const DESTINATIONS: ReadonlyArray<{
  to: SectionRoot
  label: string
  icon: Glyph
}> = [
  { to: "/agents/skills", label: "Skills", icon: Sparkles },
  { to: "/agents/automations", label: "Automations", icon: Zap },
  { to: "/agents/bots", label: "Bots", icon: Bot },
  { to: "/agents/reviews", label: "Pull Requests", icon: GitPullRequest },
  { to: "/incidents", label: "Incidents", icon: AlertTriangle },
]

/** Threads shown per repo before the group needs a "Show more". */
const REPO_PREVIEW_COUNT = 5
const NO_REPO_GROUP_KEY = "repo:no-repo"

function isOrganizeMode(value: unknown): value is OrganizeMode {
  return value === "workspace" || value === "repo" || value === "list"
}

function isChatSort(value: unknown): value is ChatSort {
  return value === "created" || value === "updated"
}

function isPinnedSort(value: unknown): value is PinnedSort {
  return value === "updated" || value === "manual"
}

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

/** The rail's agent features: one SidebarNav, the same rungs as the product rail. */
function AgentFeatures({
  login,
  compact,
  activeThreadId,
}: {
  login: string
  compact: boolean
  activeThreadId?: string
}) {
  const chat = useChatRoutes()
  const profile = useProfile()
  const { openPalette } = useAppCommandControls()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const activeSection = sectionOf(pathname)
  const conciergeMode = Boolean(profile.data?.concierge_mode)
  const concierge = useQuery({
    queryKey: ["concierge", login],
    queryFn: api.concierge,
    enabled: conciergeMode,
    refetchInterval: 30_000,
  })
  const conciergeThreadId = concierge.data?.thread_id
  const conciergeChannelId = concierge.data?.channel_id
  const onHome = pathname === chat.home || pathname === `${chat.home}/`

  return (
    <SidebarNav label="Agent features" flush>
      <SidebarNavItem
        icon={FileEdit}
        label="New Thread"
        href={chat.home}
        selected={onHome}
        compact={compact}
      />
      <SidebarNavItem
        icon={Search}
        label="Search"
        onSelect={openPalette}
        compact={compact}
      />
      {conciergeMode && (
        <SidebarNavItem
          icon={MessageCircle}
          label="Concierge"
          href={
            conciergeThreadId ? `${chat.home}/${conciergeThreadId}` : undefined
          }
          onSelect={
            !conciergeThreadId && conciergeChannelId
              ? () =>
                  window.location.assign(
                    `slack://channel?id=${conciergeChannelId}`
                  )
              : undefined
          }
          selected={
            Boolean(conciergeThreadId) && activeThreadId === conciergeThreadId
          }
          compact={compact}
        />
      )}
      {DESTINATIONS.map((destination) => {
        const active = activeSection === destination.to
        return (
          <SidebarNavItem
            key={destination.to}
            icon={destination.icon}
            label={destination.label}
            href={
              active ? destination.to : getLastSectionLocation(destination.to)
            }
            selected={active}
            compact={compact}
          />
        )
      })}
    </SidebarNav>
  )
}

/** The desktop app's pending update, beside the identity foot. */
function useDesktopUpdate() {
  const [updateState, setUpdateState] = useState<DesktopUpdateState>({
    status: "idle",
  })
  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    void desktop
      .getUpdateState()
      .then(setUpdateState)
      .catch((error: unknown) =>
        reportError({ title: "Couldn't read the app update state", error })
      )
    return desktop.onUpdateState(setUpdateState)
  }, [])
  const install = useCallback(async () => {
    const desktop = window.openSweDesktop
    if (!desktop || updateState.status !== "ready") return
    const readyState = updateState
    setUpdateState({ ...readyState, status: "installing" })
    if (await desktop.installUpdate().catch(() => false)) return
    setUpdateState((current) =>
      current.status === "installing" ? readyState : current
    )
  }, [updateState])
  return { status: updateState.status, install }
}

/** Identity and settings under the list, plus "Restart to update" on desktop. */
function AgentsRailFoot({
  user,
  collapsed,
}: {
  user: SessionUser
  collapsed: boolean
}) {
  const update = useDesktopUpdate()
  const installing = update.status === "installing"
  const label = installing ? "Installing update" : "Restart to update"
  return (
    <Stack gap="none" className="shrink-0">
      {(update.status === "ready" || installing) && (
        <Box className="px-2 pt-2">
          <Button
            size={collapsed ? "icon" : "control"}
            aria-label={label}
            title={label}
            loading={installing}
            onClick={() => void update.install()}
            className="w-full"
          >
            <Icon icon={Download} />
            {collapsed ? null : "Restart to update"}
          </Button>
        </Box>
      )}
      <SidebarUserMenu user={user} collapsed={collapsed} showSettingsLink />
    </Stack>
  )
}

/**
 * The thread rail: agent features, then the grouped thread list. Status shows
 * on each row, PINNED comes first, and repositories group the rest.
 */
function AgentsSidebar({
  login,
  activeThreadId,
  activeLocalSessionId,
  compact,
}: {
  login: string
  activeThreadId?: string
  activeLocalSessionId?: string
  compact: boolean
}) {
  const navigate = useNavigate()
  const chat = useChatRoutes()
  const queryClient = useQueryClient()
  const scrollViewport = useRef<HTMLDivElement>(null)
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
  const workspaceOrganize = prefs.organize === "workspace"
  // "workspace" mode groups the same repo folders as "repo" mode; it
  // only changes how the unpinned ones are laid out (nested under a workspace
  // header instead of a flat list), so every other repo-mode query and
  // computation below applies to both.
  const repoMode = prefs.organize === "repo" || workspaceOrganize
  const includeAutomations =
    prefs.filters.includeAutomations ||
    prefs.filters.sources.includes("schedule")
  const pinnedQuery = useSidebarPinnedThreads({ enabled: true })
  const recentsQuery = useSidebarRecents({
    repoMode,
    includeAutomations,
    includeResolved: prefs.filters.includeResolved,
    sort: prefs.sortChats,
    enabled: true,
  })
  const sidebarReposQuery = useSidebarRepos({
    includeAutomations,
    includeResolved: prefs.filters.includeResolved,
    enabled: repoMode,
  })
  const workspaceOptionsQuery = useWorkspaceOptions(workspaceOrganize)
  // One workspace — or none yet, while the list loads — has nothing to group
  // by, so the header would add a level of nesting that says nothing. The
  // composer's workspace picker and the admin page hide themselves the same way.
  const workspaceMode =
    workspaceOrganize &&
    (workspaceOptionsQuery.data?.workspaces.length ?? 0) > 1
  const localThreads = useLegacyLocalThreads({ enabled: isDesktop })
  const localSessions = localThreads.data ?? []
  const activity = useLegacyLocalActivity()
  const refreshLocalThreads = useRefreshLegacyLocalThreads()
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
    enabled: true,
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
        sidebarRepoOptions(unpinnedLocalItems, localRepos).map((repo) => ({
          ...repo,
          key: aliases.get(repo.label.trim().toLowerCase()) ?? repo.key,
        })),
        prefs.sortChats,
        true
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
            localRepoPath: localRepos.find(
              (repo) => sidebarRepoKey(repo.cwd) === group.key
            )?.cwd,
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
  // Each repository folder sits under the workspace that prefers it, even
  // though threads from other workspaces may use it; local-only folders (no
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

  const pullRequestFor = useSidebarPullRequests(allItems, true)
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
        ?.updateLegacyLocalThread({
          threadId: item.id,
          archived: !isArchived(item),
        })
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

  const renderRow = (
    item: SidebarThreadItem,
    live: PullRequestSnapshot | undefined = pullRequestFor(item)
  ) => (
    <SidebarThreadRow
      key={item.key}
      item={item}
      isActive={item.key === activeKey}
      pinned={isPinned(item)}
      archived={isArchived(item)}
      live={live}
      compact={prefs.compact}
      onDeleteLocal={refreshLocalThreads}
      onTogglePin={() => togglePin(item)}
      onToggleArchived={() => toggleArchived(item)}
    />
  )

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
      <DropdownMenuSeparator />
      <DropdownMenuSub>
        <DropdownMenuSubTrigger>
          <Icon icon={Trash2} size="sm" />
          Remove repository…
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className="w-56">
          <DropdownMenuGroup>
            {localRepos.map((repo) => (
              <DropdownMenuItem
                key={repo.cwd}
                onClick={() => void removeLocalRepo(repo.cwd)}
                variant="destructive"
              >
                <Icon icon={Trash2} size="sm" />
                <Box render={<span />} className="min-w-0 truncate">
                  {repo.name}
                </Box>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    </>
  )

  const viewMenuItems = (
    <>
      <DropdownMenuGroup>
        <DropdownMenuLabel>Organize sidebar</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={prefs.organize}
          onValueChange={(value) => {
            if (isOrganizeMode(value)) setView({ organize: value })
          }}
        >
          <DropdownMenuRadioItem value="workspace">
            Workspaces
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="repo">
            By repository
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="list">
            In one list
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuGroup>
      <DropdownMenuGroup>
        <DropdownMenuLabel>Sort chats by</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={prefs.sortChats}
          onValueChange={(value) => {
            if (isChatSort(value)) setView({ sortChats: value })
          }}
        >
          <DropdownMenuRadioItem value="created">Created</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="updated">
            Last updated
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuGroup>
      <DropdownMenuSeparator />
      <DropdownMenuGroup>
        <DropdownMenuCheckboxItem
          checked={prefs.filters.includeResolved}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeResolved: checked })
          }
        >
          Show archived
        </DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem
          checked={prefs.filters.includeAutomations}
          onCheckedChange={(checked) =>
            setFilters({ ...prefs.filters, includeAutomations: checked })
          }
        >
          Show automations
        </DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem
          checked={prefs.compact}
          onCheckedChange={setCompact}
        >
          Compact rows
        </DropdownMenuCheckboxItem>
      </DropdownMenuGroup>
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
      renderRow={renderRow}
    />
  )

  const cloudPending =
    pinnedQuery.isPending ||
    recentsQuery.isPending ||
    (repoMode && sidebarReposQuery.isPending)
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

  // The icon rail keeps one flat column of status dots: pinned first, then
  // whatever the list has loaded, with the open thread always among them.
  const dotItems = compact
    ? [
        ...filteredPinnedItems,
        ...(activeInRepo && activeThread
          ? [cloudSidebarThread(activeThread)]
          : []),
        ...recents,
      ].filter(
        (item, index, items) =>
          items.findIndex((other) => other.key === item.key) === index
      )
    : []

  return (
    <Stack
      data-slot="agents-thread-rail"
      gap="sm"
      className="h-full min-h-0 w-full px-2 pt-2 pb-2"
    >
      <AgentFeatures
        login={login}
        compact={compact}
        activeThreadId={activeThreadId}
      />
      <ScrollArea
        overflow="vertical"
        viewportRef={scrollViewport}
        className="-mr-2 min-h-0 flex-1"
      >
        {compact ? (
          <Stack
            data-slot="thread-rail-dots"
            gap="xs"
            align="stretch"
            className="pt-2 pr-2 pb-1"
          >
            {sourcesLoading && dotItems.length === 0 && (
              <ThreadListSkeleton compact />
            )}
            {dotItems.map((item) => (
              <SidebarThreadDot
                key={item.key}
                item={item}
                isActive={item.key === activeKey}
              />
            ))}
          </Stack>
        ) : (
          <Stack
            gap="sm"
            aria-busy={sourcesLoading || undefined}
            className="pt-2 pr-2 pb-1"
          >
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
              <Inline
                gap="sm"
                align="center"
                ink="ink-subtle"
                className="px-2 text-meta"
              >
                <Spinner size="sm" />
                Loading threads…
              </Inline>
            )}

            {(filteredPinnedItems.length > 0 ||
              pinnedGroups.length > 0 ||
              (repoMode && noRepoPinned && noRepoAvailable)) && (
              <SidebarSection
                label="Pinned"
                collapsed={sectionCollapsed("pinned")}
                onToggleCollapsed={() => toggleSectionCollapsed("pinned")}
                menu={
                  <SidebarSectionMenu label="Pinned options">
                    <DropdownMenuGroup>
                      <DropdownMenuLabel>Sort pinned by</DropdownMenuLabel>
                      <DropdownMenuRadioGroup
                        value={prefs.sortPinned}
                        onValueChange={(value) => {
                          if (isPinnedSort(value))
                            setView({ sortPinned: value })
                        }}
                      >
                        <DropdownMenuRadioItem value="updated">
                          Last updated
                        </DropdownMenuRadioItem>
                        <DropdownMenuRadioItem value="manual">
                          Manual order
                        </DropdownMenuRadioItem>
                      </DropdownMenuRadioGroup>
                    </DropdownMenuGroup>
                  </SidebarSectionMenu>
                }
              >
                {filteredPinnedItems.map((item) => renderRow(item))}
                {pinnedGroups.map(renderRepoGroup)}
                {repoMode &&
                  noRepoPinned &&
                  noRepoAvailable &&
                  renderRepoGroup(noRepoGroup)}
              </SidebarSection>
            )}

            {repoMode &&
              (unpinnedGroups.length > 0 || noRepoAvailable || isDesktop) && (
                <SidebarSection
                  label={workspaceMode ? "Workspaces" : "Repositories"}
                  collapsed={sectionCollapsed("repos")}
                  onToggleCollapsed={() => toggleSectionCollapsed("repos")}
                  menu={
                    <SidebarSectionMenu
                      label="Repositories options"
                      icon={Filter}
                      reveal={false}
                    >
                      {viewMenuItems}
                      {removeProjectItems}
                    </SidebarSectionMenu>
                  }
                  action={
                    isDesktop ? (
                      <SidebarSectionAction
                        label="Add repository"
                        icon={Plus}
                        onClick={() => void addLocalRepo()}
                      />
                    ) : undefined
                  }
                >
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
                </SidebarSection>
              )}

            {!repoMode && (
              <SidebarSection
                label="Recents"
                collapsed={sectionCollapsed("recents")}
                onToggleCollapsed={() => toggleSectionCollapsed("recents")}
                menu={
                  <SidebarSectionMenu
                    label="Recents options"
                    icon={Filter}
                    reveal={false}
                  >
                    {viewMenuItems}
                  </SidebarSectionMenu>
                }
              >
                {recents.map((item) => renderRow(item))}
                {recentsQuery.hasMore && (
                  <LoadMoreThreads
                    root={scrollViewport}
                    loading={recentsQuery.isFetchingNextPage}
                    onLoadMore={recentsQuery.fetchNextPage}
                  />
                )}
              </SidebarSection>
            )}
            {isEmpty && !cloudError && !localThreads.isError && (
              <Box render={<p />} className="px-2 text-meta text-ink-subtle">
                {hasActiveFilters(prefs.filters)
                  ? "No threads match these filters."
                  : "No threads yet."}
              </Box>
            )}
          </Stack>
        )}
      </ScrollArea>
    </Stack>
  )
}

/**
 * A workspace inside the "Workspaces" section, nesting the repo folders that
 * belong to it. Collapse state reuses the sidebar's generic collapsed-section
 * keys (`workspace:<slug>`), the same mechanism the section headers use.
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
  renderRepoGroup: (group: HydratedRepoGroup) => ReactNode
}) {
  return (
    <SidebarTreeGroup
      label={workspace.name}
      kind="directory"
      glyph={StackGlyph}
      open={!collapsed}
      onOpenChange={(open) => {
        if (open === collapsed) onToggleCollapsed()
      }}
    >
      <Stack gap="none" className="gap-0.5">
        {workspace.repos.map(renderRepoGroup)}
      </Stack>
    </SidebarTreeGroup>
  )
}

/**
 * A repository folder: its identity glyph opens and closes it, Pin and
 * Compose appear on the heading under the pointer, and its threads page in
 * on their own.
 */
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
  ) => ReactNode
}) {
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
    <SidebarTreeGroup
      label={group.label}
      kind="folder"
      open={!collapsed}
      onOpenChange={(open) => {
        if (open === collapsed) onToggleCollapsed()
      }}
      trailing={
        <>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={pinned ? `Unpin ${group.label}` : `Pin ${group.label}`}
            title={pinned ? "Unpin repository" : "Pin repository"}
            onClick={onTogglePin}
            className={REVEAL_ON_HEADING_CLASS}
          >
            <Icon
              icon={pinned ? PinOff : PushPin}
              size="sm"
              className="text-ink-subtle"
            />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Compose message in ${group.label}`}
            title="Compose message"
            onClick={onCompose}
            className={REVEAL_ON_HEADING_CLASS}
          >
            <Icon icon={FileEdit} size="sm" className="text-ink-subtle" />
          </Button>
        </>
      }
    >
      <Stack gap="none" className="gap-0.5">
        {shown.map((item) => renderRow(item, pullRequestFor(item)))}
        {shown.length === 0 && loading && <ThreadListSkeleton rows={2} />}
        {shown.length === 0 && !loading && !repo.isError && (
          <Box render={<p />} className="px-2 py-1 text-meta text-ink-subtle">
            No chats
          </Box>
        )}
        {repo.isError && (
          <ThreadSourceError
            label="Chats unavailable"
            retryLabel="Retry loading chats"
            onRetry={() => void repo.refetch()}
          />
        )}
        {hasMore && (
          <Button
            variant="ghost"
            size="compact"
            loading={loading}
            onClick={() => {
              if (!expanded) onExpand()
              else if (onLoadMore) onLoadMore()
              else repo.fetchNextPage()
            }}
            className="w-full justify-start px-2 font-normal text-ink-subtle"
          >
            Show more
          </Button>
        )}
      </Stack>
    </SidebarTreeGroup>
  )
}

/**
 * Keeps the rail's headers mounted and draws rows where threads will land, so
 * the sidebar reads as loading rather than as an account with no threads.
 */
function ThreadListSkeleton({
  compact = false,
  rows,
}: {
  compact?: boolean
  rows?: number
}) {
  const widths = ["w-3/4", "w-1/2", "w-2/3", "w-3/5", "w-4/5"].slice(
    0,
    rows ?? 5
  )
  return (
    <Stack
      data-testid={rows === undefined ? "sidebar-threads-skeleton" : undefined}
      gap="none"
      className="gap-0.5 px-2"
    >
      {rows === undefined && (
        <Box render={<span />} role="status" className="sr-only">
          Loading threads
        </Box>
      )}
      {widths.map((width) => (
        <Inline
          key={width}
          aria-hidden
          align="center"
          className={compact ? "h-control-sm" : "h-control"}
        >
          <Skeleton className={cn("h-3", width)} />
        </Inline>
      ))}
    </Stack>
  )
}

function ThreadSourceError({
  label,
  retryLabel = "Retry",
  onRetry,
}: {
  label: string
  retryLabel?: string
  onRetry: () => void
}) {
  return (
    <Inline gap="sm" align="center" className="pl-2 text-meta">
      <Icon icon={AlertTriangle} size="sm" className="text-attention" />
      <Box
        render={<span />}
        className="min-w-0 flex-1 truncate text-ink-subtle"
      >
        {label}
      </Box>
      <Button
        variant="ghost"
        size="compact"
        aria-label={retryLabel}
        onClick={onRetry}
      >
        Retry
      </Button>
    </Inline>
  )
}

/** The end-of-list control: loads as it scrolls into view, and stays operable directly. */
function LoadMoreThreads({
  root,
  loading,
  onLoadMore,
}: {
  root: RefObject<HTMLDivElement | null>
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
      ref={sentinel}
      variant="ghost"
      size="compact"
      loading={loading}
      onClick={() => load.current()}
      className="w-full font-normal text-ink-subtle"
    >
      Load more threads
    </Button>
  )
}

/**
 * The agents surface's chrome: the system AppShell in focus posture, with the
 * thread rail where the navigation rail would be, the brand row above it and
 * the identity foot below.
 */
export function AgentsShell({
  user,
  activeThreadId,
  activeLocalSessionId,
  children,
}: {
  user: SessionUser
  activeThreadId?: string
  activeLocalSessionId?: string
  children: ReactNode
}) {
  const rail = useRailCollapsed()
  const toggleRail = rail.toggle
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
        run: toggleRail,
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
  }, [activeThread, toggleRail, pinThread, pinnedThreads.data, resolveThread])
  useRegisterAppCommands(sidebarCommands)

  return (
    <Box className="h-svh">
      <AppShell
        mode="focus"
        contentWidth="wide"
        railCollapsed={rail.collapsed}
        railHeader={
          <AppRailBrand
            collapsed={rail.collapsed}
            onToggleCollapsed={toggleRail}
          />
        }
        railFooter={<AgentsRailFoot user={user} collapsed={rail.collapsed} />}
        threadRail={
          <AgentsSidebar
            login={user.login}
            activeThreadId={activeThreadId}
            activeLocalSessionId={activeLocalSessionId}
            compact={rail.collapsed}
          />
        }
      >
        <Inline
          align="stretch"
          className="relative h-full min-h-0 min-w-0 overflow-hidden"
        >
          {children}
        </Inline>
      </AppShell>
    </Box>
  )
}

/**
 * The shell's geometry before the session is known: the desk, the rail at its
 * resting width and an empty work column, so nothing jumps when it arrives.
 */
export function AgentsShellPlaceholder() {
  return (
    <Inline
      align="stretch"
      aria-busy="true"
      aria-label="Loading"
      className="h-svh bg-desk"
    >
      <Stack
        gap="sm"
        bg="sidebar"
        className="w-61.5 shrink-0 border-r border-line px-3 pt-2"
      >
        <Inline align="center" className="h-control">
          <Skeleton className="h-4 w-24" />
        </Inline>
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </Stack>
      <Box className="flex-1 bg-shell" />
    </Inline>
  )
}
