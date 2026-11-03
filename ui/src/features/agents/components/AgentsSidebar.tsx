import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Spinner, SpinnerIcon } from "@langchain/macaw-components/Spinner"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import {
  MagnifyingGlassRegularIcon,
  PlusIcon,
  CaretDownIcon,
  CaretRightIcon,
} from "@langchain/macaw-components/icons"
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/ssr/DownloadSimple"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { LogoMark } from "@/components/Branding"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  SidebarLayoutProvider,
  useSidebarLayout,
  type SidebarLayout,
} from "@/components/sidebar-layout"
import type { DesktopUpdateState } from "@/desktop"
import type { SessionUser } from "@/lib/api"
import {
  useAppCommandControls,
  useRegisterAppCommands,
} from "@/lib/appCommands"
import { useShortcutLabel } from "@/lib/hotkeys"
import { usePendingVariables } from "@/lib/optimistic"
import {
  useChatProjects,
  useReorderPinnedChats,
} from "@/features/agents/lib/chatProjects"
import {
  agentMutationKeys,
  agentThreadKeys,
  useActiveAgentThread,
  usePinAgentThread,
  useResolveAgentThread,
  useSeedAgentThreadDetails,
  useSidebarActiveThread,
  useSidebarPinnedThreads,
  useSidebarRecents,
} from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  cloudSidebarThread,
  withoutNestedWorkers,
} from "@/features/agents/lib/sidebarThreads"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"
import { reviewPageRoute } from "@/features/reviews/lib/reviewEntry"
import { SidebarThreadRow } from "./SidebarThreadRow"
import { SidebarNav } from "./SidebarNav"
import {
  SidebarSectionAction,
  SidebarSectionHeader,
} from "./SidebarSectionHeader"
import { ProjectDialog } from "./projects/ProjectDialog"
import { ProjectMenu } from "./projects/ProjectMenu"
import { ProjectThreads } from "./projects/ProjectThreads"
import { cn } from "@/lib/utils"

export function AgentsSidebar({
  user,
  activeThreadId,
  layout,
}: {
  user: SessionUser
  activeThreadId?: string
  layout: SidebarLayout
}) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const { openPalette } = useAppCommandControls()
  const shortcut = useShortcutLabel("mod+n")
  const { prefs, toggleSectionCollapsed } = useSidebarPrefs()
  const [projectOpen, setProjectOpen] = useState(false)
  const [archivedClosed, setArchivedClosed] = useState(true)
  const [closedProjects, setClosedProjects] = useState<string[]>([])
  const projects = useChatProjects()
  const pinned = useSidebarPinnedThreads()
  const recents = useSidebarRecents({
    repoMode: false,
    includeAutomations: true,
    includeResolved: true,
    owned: false,
    includeClaudeCode: true,
    sort: "updated",
  })
  const pin = usePinAgentThread()
  const archive = useResolveAgentThread()
  const reorder = useReorderPinnedChats()
  const pendingPins = usePendingVariables<{ threadId: string }>(
    agentMutationKeys.pin
  )
  const pendingArchives = usePendingVariables<{ threadId: string }>(
    agentMutationKeys.resolve
  )
  const pinnedIds = new Set((pinned.data ?? []).map((thread) => thread.id))
  const loaded = [...(pinned.data ?? []), ...recents.items]
  const active = useSidebarActiveThread({
    activeThreadId,
    loadedThreads: loaded,
    enabled: true,
  })
  const all = withoutNestedWorkers([
    ...new Map(
      [...(active ? [active] : []), ...loaded].map((thread) => [
        thread.id,
        thread,
      ])
    ).values(),
  ])
  useSeedAgentThreadDetails(all, activeThreadId)
  const openThread = useCallback(
    (threadId: string) => {
      const review = client.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )?.reviewPage
      if (review) void navigate(reviewPageRoute(review))
      else void navigate({ to: "/agents/$threadId", params: { threadId } })
    },
    [client, navigate]
  )
  useRunCompletionNotifier(all, activeThreadId, openThread)
  const sorted = [...all].sort(
    (left, right) =>
      new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime()
  )
  const pinnedThreads = withoutNestedWorkers(pinned.data ?? []).filter(
    (thread) => !thread.resolved
  )
  const recentThreads = sorted.filter(
    (thread) => !thread.resolved && !pinnedIds.has(thread.id)
  )
  const archivedThreads = sorted.filter((thread) => thread.resolved)
  const [update, setUpdate] = useState<DesktopUpdateState>({ status: "idle" })
  useEffect(() => {
    const desktop = window.openSweDesktop
    if (!desktop) return
    void desktop.getUpdateState().then(setUpdate)
    return desktop.onUpdateState(setUpdate)
  }, [])
  const installUpdate = async () => {
    const desktop = window.openSweDesktop
    if (!desktop || update.status !== "ready") return
    const ready = update
    setUpdate({ ...ready, status: "installing" })
    if (await desktop.installUpdate().catch(() => false)) return
    setUpdate(ready)
  }
  const row = (thread: AgentThread, recent = false, index?: number) => {
    const projectId = projects.data?.memberships[thread.id]
    const busy =
      pendingPins.some((item) => item.threadId === thread.id) ||
      pendingArchives.some((item) => item.threadId === thread.id)
    const move = (offset: number) => {
      const ids = pinnedThreads.map((item) => item.id)
      const from = ids.indexOf(thread.id)
      const to = from + offset
      if (from < 0 || to < 0 || to >= ids.length) return
      ;[ids[from], ids[to]] = [ids[to]!, ids[from]!]
      reorder.mutate(ids)
    }
    return (
      <SidebarThreadRow
        key={thread.id}
        item={cloudSidebarThread(thread)}
        isActive={thread.id === activeThreadId}
        pinned={pinnedIds.has(thread.id)}
        archived={!!thread.resolved}
        pending={busy || reorder.isPending}
        onNavigate={layout.closeOnMobile}
        onTogglePin={() => {
          if (!busy)
            pin.mutate({
              threadId: thread.id,
              pinned: !pinnedIds.has(thread.id),
            })
        }}
        onToggleArchived={() => {
          if (!busy)
            archive.mutate({ threadId: thread.id, resolved: !thread.resolved })
        }}
        projectLabel={
          recent
            ? projects.data?.projects.find(
                (project) => project.id === projectId
              )?.name
            : undefined
        }
        onMoveUp={index !== undefined && index > 0 ? () => move(-1) : undefined}
        onMoveDown={
          index !== undefined && index < pinnedThreads.length - 1
            ? () => move(1)
            : undefined
        }
      />
    )
  }
  const loading = pinned.isPending || recents.isPending
  const error = pinned.isError || recents.isError
  return (
    <SidebarFrame {...layout} className="border-r border-default bg-sidebar">
      <div
        className={cn(
          "flex items-center justify-between px-space-4 pb-space-4",
          window.openSweDesktop ? "pt-13" : "pt-space-4"
        )}
      >
        <Link
          to="/agents"
          className="flex min-w-0 items-center gap-space-2 font-heading text-sm font-medium text-primary"
        >
          <LogoMark alt="" className="size-5" />
          Open SWE
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>
      <div className="flex flex-col gap-space-1 px-space-2 pb-space-3">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="group flex h-8 items-center gap-space-2 rounded-lg px-space-2 text-sm text-primary hover:bg-surface-level-2-hover"
        >
          <PlusIcon size={16} weight="regular" />
          New chat
          <Kbd
            aria-hidden
            className="ml-auto opacity-0 group-hover:opacity-100"
          >
            {shortcut}
          </Kbd>
        </Link>
        <Button
          size="sm"
          color="secondary"
          variant="plain"
          className="justify-start gap-space-2 text-secondary"
          aria-label="Search chats"
          onClick={() => {
            layout.closeOnMobile()
            openPalette()
          }}
        >
          <MagnifyingGlassRegularIcon size={16} weight="regular" />
          Search
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-space-2 pb-space-2">
        {loading && all.length === 0 && (
          <div
            role="status"
            className="flex items-center gap-space-2 px-space-2 text-xs text-secondary"
          >
            <Spinner size="xs" />
            Loading chats…
          </div>
        )}
        {error && (
          <div role="alert" className="text-error px-space-2 text-xs">
            Chats unavailable.
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              onClick={() => {
                void pinned.refetch()
                void recents.refetch()
              }}
            >
              Retry
            </Button>
          </div>
        )}
        {pinnedThreads.length > 0 && (
          <section className="mb-space-3">
            <SidebarSectionHeader
              label="Pinned"
              collapsed={prefs.collapsedSectionKeys.includes("pinned")}
              onToggleCollapsed={() => toggleSectionCollapsed("pinned")}
            />
            {!prefs.collapsedSectionKeys.includes("pinned") &&
              pinnedThreads.map((thread, index) => row(thread, false, index))}
          </section>
        )}
        {projects.isError && (
          <div role="alert" className="text-error px-space-2 text-xs">
            Projects unavailable.
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              onClick={() => void projects.refetch()}
            >
              Retry
            </Button>
          </div>
        )}
        {!!projects.data?.projects.length && (
          <section className="mb-space-3">
            <SidebarSectionHeader
              label="Projects"
              collapsed={prefs.collapsedSectionKeys.includes("projects")}
              onToggleCollapsed={() => toggleSectionCollapsed("projects")}
              action={
                <SidebarSectionAction
                  label="New project"
                  icon={PlusIcon}
                  onClick={() => setProjectOpen(true)}
                />
              }
            />
            {!prefs.collapsedSectionKeys.includes("projects") &&
              [...projects.data.projects]
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((project) => {
                  const closed = closedProjects.includes(project.id)
                  return (
                    <div key={project.id} className="mb-space-1">
                      <div className="group/project flex items-center gap-space-1 rounded-lg hover:bg-surface-level-2-hover">
                        <IconButton
                          icon={closed ? CaretRightIcon : CaretDownIcon}
                          label={`${closed ? "Expand" : "Collapse"} ${project.name}`}
                          size="xs"
                          color="secondary"
                          variant="plain"
                          aria-expanded={!closed}
                          onClick={() =>
                            setClosedProjects((current) =>
                              closed
                                ? current.filter((id) => id !== project.id)
                                : [...current, project.id]
                            )
                          }
                        />
                        <Link
                          to="/agents"
                          search={{ project: project.id }}
                          onClick={layout.closeOnMobile}
                          className="flex min-w-0 flex-1 items-center gap-space-2 py-space-1 text-sm text-secondary"
                        >
                          <FolderIcon
                            size={14}
                            weight="regular"
                            className="shrink-0"
                          />
                          <span className="truncate">{project.name}</span>
                        </Link>
                        <ProjectMenu
                          project={project}
                          className="opacity-0 group-focus-within/project:opacity-100 group-hover/project:opacity-100 data-[state=open]:opacity-100"
                        />
                      </div>
                      {!closed && (
                        <div className="pl-space-3">
                          <ProjectThreads
                            projectId={project.id}
                            activeThreadId={activeThreadId}
                            onNavigate={layout.closeOnMobile}
                            preview
                          />
                        </div>
                      )}
                    </div>
                  )
                })}
          </section>
        )}
        {recentThreads.length > 0 && (
          <section className="mb-space-3">
            <SidebarSectionHeader
              label="Recents"
              collapsed={prefs.collapsedSectionKeys.includes("recents")}
              onToggleCollapsed={() => toggleSectionCollapsed("recents")}
            />
            {!prefs.collapsedSectionKeys.includes("recents") &&
              recentThreads.map((thread) => row(thread, true))}
          </section>
        )}
        {archivedThreads.length > 0 && (
          <section className="mb-space-3">
            <SidebarSectionHeader
              label="Archived"
              collapsed={archivedClosed}
              onToggleCollapsed={() => setArchivedClosed((closed) => !closed)}
            />
            {!archivedClosed && archivedThreads.map((thread) => row(thread))}
          </section>
        )}
        {recents.hasMore && (
          <LoadMoreChats
            loading={recents.isFetchingNextPage}
            onLoadMore={recents.fetchNextPage}
          />
        )}
        {!loading && !error && all.length === 0 && (
          <p className="px-space-2 py-space-4 text-xs text-tertiary">
            Your chats will appear here.
          </p>
        )}
      </div>
      <div className="px-space-2">
        <SidebarNav
          onNavigate={layout.closeOnMobile}
          onNewProject={() => setProjectOpen(true)}
        />
      </div>
      <div className="flex items-center gap-space-2 p-space-2">
        <div className="min-w-0 flex-1">
          <SidebarUserMenu user={user} showSettingsLink />
        </div>
        {(update.status === "ready" || update.status === "installing") && (
          <Button
            color="primary"
            size="sm"
            aria-label={
              update.status === "installing"
                ? "Installing update"
                : "Restart to update"
            }
            disabled={update.status === "installing"}
            onClick={() => void installUpdate()}
            leftDecorator={
              update.status === "installing" ? SpinnerIcon : DownloadSimpleIcon
            }
          >
            {update.status === "installing"
              ? "Installing…"
              : "Restart to update"}
          </Button>
        )}
      </div>
      <ProjectDialog open={projectOpen} onOpenChange={setProjectOpen} />
    </SidebarFrame>
  )
}

function LoadMoreChats({
  loading,
  onLoadMore,
}: {
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
        if (!loading && entries.some((entry) => entry.isIntersecting))
          load.current()
      },
      { root: node.parentElement, rootMargin: "200px" }
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [loading])
  return (
    <Button
      ref={sentinel}
      size="xs"
      color="secondary"
      variant="plain"
      className="w-full"
      disabled={loading}
      onClick={() => load.current()}
    >
      {loading ? "Loading…" : "Show more chats"}
    </Button>
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
  const layout = useSidebarLayout()
  // `useMutation` returns a fresh object every render; only `mutate` is stable,
  // and an unstable command array re-registers on every commit.
  const pinThread = usePinAgentThread().mutate
  const resolveThread = useResolveAgentThread().mutate
  const pinnedThreads = useSidebarPinnedThreads({
    enabled: Boolean(activeThreadId),
  })
  const activeThread = useActiveAgentThread(activeThreadId, {
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
      <TooltipProvider delayDuration={500} skipDelayDuration={100}>
        <div className="agents-ui flex h-svh overflow-hidden bg-surface-level-1">
          <AgentsSidebar
            user={user}
            activeThreadId={activeThreadId}
            layout={layout}
          />
          <main className="relative flex min-w-0 flex-1 overflow-hidden bg-surface-level-1">
            {children}
          </main>
        </div>
      </TooltipProvider>
    </SidebarLayoutProvider>
  )
}
