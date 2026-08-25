import { ContextMenu } from "@base-ui/react/context-menu"
import { Link } from "@tanstack/react-router"
import {
  ArrowCounterClockwiseIcon,
  CalendarBlankIcon,
  ChatCircleIcon,
  CheckCircleIcon,
  CircleNotchIcon,
  CloudIcon,
  CopyIcon,
  DesktopIcon,
  FolderOpenIcon,
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
import { useMemo, useState } from "react"
import type { ComponentType, SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { DesktopLocalThreadSummary, DesktopProject } from "@/desktop"
import type { AgentSource, AgentThread } from "@/features/agents/lib/types"
import { NEW_TAB_PATH } from "@/features/agents/lib/tabs"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { SidebarFilterMenu } from "@/features/agents/components/SidebarFilterMenu"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import { Button, buttonVariants } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  availableFacets,
  filterThreads,
  hasActiveFilters,
} from "@/features/agents/lib/sidebarFilter"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  useDeleteAgentThread,
  useResolveAgentThread,
  useSidebarThreads,
} from "@/features/agents/lib/queries"
import {
  useDesktopLocalThreads,
  useLocalThreadActivity,
  useRefreshLocalThreads,
} from "@/features/agents/lib/desktopLocal"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import { cn, formatRelativeTime } from "@/lib/utils"

const NO_LOCAL_SESSIONS: Array<never> = []

type Icon = ComponentType<SVGProps<SVGSVGElement>>

const SOURCE_META: Record<AgentSource, { icon: Icon; label: string }> = {
  dashboard: { icon: ChatCircleIcon, label: "Started from the dashboard" },
  github: { icon: IoLogoGithub, label: "Triggered from GitHub" },
  slack: { icon: IoLogoSlack, label: "Triggered from Slack" },
  linear: { icon: SiLinear, label: "Triggered from Linear" },
  schedule: { icon: CalendarBlankIcon, label: "Triggered from a schedule" },
}

type PrState = NonNullable<AgentThread["pr"]>["state"]

const PR_STATE_META: Record<
  PrState,
  { icon: Icon; label: string; className: string }
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

const NAV = [
  { to: "/agents/threads", label: "Kanban", icon: Kanban },
  { to: "/agents/skills", label: "Skills", icon: SparkleIcon },
  { to: "/agents/automations", label: "Automations", icon: LightningIcon },
  { to: "/agents/reviews", label: "Reviews", icon: GitPullRequestIcon },
] as const

type SessionRow =
  | { kind: "cloud"; id: string; sortedAt: number; thread: AgentThread }
  | {
      kind: "local"
      id: string
      sortedAt: number
      session: DesktopLocalThreadSummary
    }

function projectName(cwd: string, projects: Array<DesktopProject>) {
  const project = projects.find((item) => item.cwd === cwd)
  return project?.name ?? cwd.split("/").filter(Boolean).at(-1) ?? cwd
}

function matchesQuery(haystack: Array<string>, query: string) {
  if (!query) return true
  const needle = query.toLowerCase()
  return haystack.some((value) => value.toLowerCase().includes(needle))
}

/**
 * The full-page session list: every cloud thread and every session running on
 * this computer in one time-ordered list, each row marked with the icon for
 * where it runs.
 */
export function SessionsHome({
  user,
  localOnly = false,
}: {
  user: SessionUser | null
  localOnly?: boolean
}) {
  const [query, setQuery] = useState("")
  const [projectFilter, setProjectFilter] = useState<string | null>(null)
  const { prefs, setGroup, setCompact, setFilters, resetFilters } =
    useSidebarPrefs()
  const cloud = useSidebarThreads({
    includeAutomations:
      prefs.filters.includeAutomations ||
      prefs.filters.sources.includes("schedule"),
    includeResolved: prefs.filters.includeResolved,
    enabled: !localOnly,
  })
  const localSessions = useDesktopLocalThreads().data ?? NO_LOCAL_SESSIONS
  const localActivity = useLocalThreadActivity()
  const refreshLocalThreads = useRefreshLocalThreads()
  const { projects, addProject, removeProject } = useDesktopProjects()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)

  const deleteLocalSession = async (sessionId: string) => {
    const deleted =
      (await window.openSweDesktop?.deleteLocalThread(sessionId)) ?? false
    if (deleted) refreshLocalThreads()
    return deleted
  }

  const cloudThreads = useMemo(
    () => [...cloud.data.active.items, ...cloud.data.resolved.items],
    [cloud.data.active.items, cloud.data.resolved.items]
  )
  const facets = useMemo(() => {
    const loaded = availableFacets(cloudThreads)
    return {
      models: [...new Set([...prefs.filters.models, ...loaded.models])].sort(
        (a, b) => a.localeCompare(b)
      ),
      repos: [...new Set([...prefs.filters.repos, ...loaded.repos])].sort(
        (a, b) => a.localeCompare(b)
      ),
    }
  }, [cloudThreads, prefs.filters.models, prefs.filters.repos])

  const rows = useMemo(() => {
    const cloudRows: Array<SessionRow> = projectFilter
      ? []
      : filterThreads(cloudThreads, prefs.filters)
          .filter((thread) =>
            matchesQuery([thread.title, thread.repoFullName], query)
          )
          .map((thread) => ({
            kind: "cloud" as const,
            id: thread.id,
            sortedAt: thread.updatedAt || thread.createdAt,
            thread,
          }))
    const localRows: Array<SessionRow> = localSessions
      .filter((session) => !projectFilter || session.cwd === projectFilter)
      .filter((session) =>
        matchesQuery([session.title, projectName(session.cwd, projects)], query)
      )
      .map((session) => ({
        kind: "local" as const,
        id: session.id,
        sortedAt: session.updatedAt || session.createdAt,
        session,
      }))
    return [...cloudRows, ...localRows].sort(
      (left, right) => right.sortedAt - left.sortedAt
    )
  }, [
    cloudThreads,
    localSessions,
    prefs.filters,
    projectFilter,
    projects,
    query,
  ])

  const isEmpty = rows.length === 0 && (localOnly || !cloud.isPending)

  return (
    <div className="h-full min-h-0 w-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-12 px-8 pt-16 pb-20">
        <div className="relative w-full">
          <MagnifyingGlassIcon className="absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground/70" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sessions"
            aria-label="Search sessions"
            className="h-10 w-full rounded-lg border border-border bg-background pr-3 pl-10 text-[13px] outline-none placeholder:text-muted-foreground/70 focus-visible:ring-2 focus-visible:ring-ring/40"
          />
        </div>

        <div className="flex flex-col gap-12 md:flex-row md:gap-16">
          <div className="flex shrink-0 flex-col gap-6 md:w-52">
            {isDesktop && (
              <section className="flex flex-col gap-1">
                <div className="flex items-center justify-between px-2 pb-1">
                  <h2 className="text-[11px] font-medium tracking-wide text-muted-foreground/70 uppercase">
                    Projects
                  </h2>
                  <button
                    type="button"
                    aria-label="Add project"
                    title="Add project"
                    onClick={() => void addProject()}
                    className="flex size-5 items-center justify-center rounded text-muted-foreground/70 hover:bg-sidebar-row-hover hover:text-foreground"
                  >
                    <PlusIcon className="size-3.5" />
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => setProjectFilter(null)}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                    projectFilter === null
                      ? "bg-sidebar-row-hover font-medium text-foreground"
                      : "text-muted-foreground hover:bg-sidebar-row-hover hover:text-foreground"
                  )}
                >
                  <FolderOpenIcon className="size-4" />
                  All sessions
                </button>
                {projects.map((project) => (
                  <div
                    key={project.cwd}
                    className="group/project flex items-center"
                  >
                    <button
                      type="button"
                      onClick={() => setProjectFilter(project.cwd)}
                      title={project.cwd}
                      className={cn(
                        "flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                        projectFilter === project.cwd
                          ? "bg-sidebar-row-hover font-medium text-foreground"
                          : "text-muted-foreground hover:bg-sidebar-row-hover hover:text-foreground"
                      )}
                    >
                      <DesktopIcon className="size-4 shrink-0" />
                      <span className="min-w-0 flex-1 truncate">
                        {project.name}
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove ${project.name}`}
                      title="Remove project"
                      onClick={() => void removeProject(project.cwd)}
                      className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground/60 opacity-0 transition-opacity group-hover/project:opacity-100 hover:bg-sidebar-row-hover hover:text-destructive focus-visible:opacity-100 [@media(hover:none)]:opacity-100"
                    >
                      <TrashIcon className="size-3.5" />
                    </button>
                  </div>
                ))}
                {projects.length === 0 && (
                  <p className="px-2 py-1.5 text-xs text-muted-foreground/70">
                    No projects yet
                  </p>
                )}
              </section>
            )}

            {!localOnly && (
              <nav className="flex flex-col gap-1">
                {NAV.map((item) => {
                  const NavIcon = item.icon
                  return (
                    <Link
                      key={item.to}
                      to={item.to}
                      className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
                    >
                      <NavIcon className="size-4" />
                      {item.label}
                    </Link>
                  )
                })}
              </nav>
            )}

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

          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex items-center justify-between gap-2 px-2 pb-1">
              <h2 className="text-[11px] font-medium tracking-wide text-muted-foreground/70 uppercase">
                Recent sessions
              </h2>
              <div className="flex items-center gap-1">
                {!localOnly && (
                  <SidebarFilterMenu
                    prefs={prefs}
                    facets={facets}
                    onGroupChange={setGroup}
                    onFiltersChange={setFilters}
                    onCompactChange={setCompact}
                    onResetFilters={resetFilters}
                  />
                )}
                <Link
                  to={NEW_TAB_PATH}
                  className={cn(
                    buttonVariants({ variant: "ghost", size: "sm" }),
                    "shrink-0 text-muted-foreground hover:text-foreground"
                  )}
                >
                  <PlusIcon className="size-4" />
                  New session
                </Link>
              </div>
            </div>

            {!localOnly && cloud.isPending && <SessionListSkeleton />}
            {rows.map((row) =>
              row.kind === "cloud" ? (
                <CloudSessionRow key={row.id} thread={row.thread} />
              ) : (
                <LocalSessionRow
                  key={row.id}
                  session={row.session}
                  projectLabel={projectName(row.session.cwd, projects)}
                  running={localActivity[row.session.id] === "running"}
                  onDelete={deleteLocalSession}
                />
              )
            )}
            {isEmpty && (
              <p className="px-2 py-16 text-center text-xs text-muted-foreground/70">
                {query || hasActiveFilters(prefs.filters) || projectFilter
                  ? "No sessions match this search."
                  : "No sessions yet. Start one with New session."}
              </p>
            )}
            {!localOnly && !cloud.isPending && cloud.data.active.hasMore && (
              <div className="flex justify-center pt-4">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={cloud.activeQuery.isFetchingNextPage}
                  onClick={() => void cloud.activeQuery.fetchNextPage()}
                >
                  {cloud.activeQuery.isFetchingNextPage
                    ? "Loading…"
                    : "Load more sessions"}
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function SessionListSkeleton() {
  return (
    <div className="flex flex-col gap-2 py-2">
      {[80, 60, 70, 50, 65].map((width, index) => (
        <div key={index} className="flex items-center gap-3 px-3 py-2">
          <Skeleton className="size-4 rounded-full" />
          <Skeleton className="h-3 rounded" style={{ width: `${width}%` }} />
        </div>
      ))}
    </div>
  )
}

const ROW_CLASS =
  "flex h-12 items-center gap-3 rounded-lg px-3 text-muted-foreground transition-colors group-hover:bg-sidebar-row-hover"

const MENU_POPUP_CLASS =
  "min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"

const MENU_ITEM_CLASS =
  "flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"

function StatusDot({ attention }: { attention: boolean }) {
  return (
    <span
      className={cn(
        "size-2 shrink-0 rounded-full",
        attention ? "bg-primary" : "bg-border"
      )}
      aria-label={attention ? "Finished" : "Viewed"}
    />
  )
}

function CloudSessionRow({ thread }: { thread: AgentThread }) {
  const deleteThread = useDeleteAgentThread()
  const resolveThread = useResolveAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const isDeleting =
    deleteThread.isPending && deleteThread.variables === thread.id
  const isReadOnly = thread.isOwner === false
  const isResolved = thread.resolved === true
  const source =
    thread.source && thread.source !== "dashboard"
      ? SOURCE_META[thread.source]
      : null
  const SourceIcon = source?.icon
  const prMeta = thread.pr ? PR_STATE_META[thread.pr.state] : null
  const PrIcon = prMeta?.icon
  const badge =
    thread.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null

  return (
    <>
      <ContextMenu.Root onOpenChange={setContextMenuOpen}>
        <ContextMenu.Trigger
          className={cn("group relative", isDeleting && "opacity-50")}
        >
          <Link
            to="/agents/$threadId"
            params={{ threadId: thread.id }}
            onClick={(event: React.MouseEvent) => {
              if (contextMenuOpen) event.preventDefault()
            }}
            className={cn(
              ROW_CLASS,
              thread.adminThread &&
                "bg-destructive/5 group-hover:bg-destructive/10"
            )}
          >
            {thread.status === "running" ? (
              <CircleNotchIcon
                className="size-4 shrink-0 animate-spin text-primary"
                aria-label="Session running"
              />
            ) : (
              <CloudIcon className="size-4 shrink-0 text-muted-foreground/80">
                <title>Cloud session</title>
              </CloudIcon>
            )}
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-[13px] text-foreground">
                {thread.title}
              </span>
              <span className="truncate text-[11px] text-muted-foreground/70">
                {thread.repoFullName || thread.repo}
                {thread.branch ? ` · ${thread.branch}` : ""}
              </span>
            </div>
            {source && SourceIcon && (
              <SourceIcon className="size-3.5 shrink-0 text-muted-foreground/70">
                <title>{source.label}</title>
              </SourceIcon>
            )}
            {prMeta && PrIcon && (
              <PrIcon className={cn("size-3.5 shrink-0", prMeta.className)}>
                <title>{prMeta.label}</title>
              </PrIcon>
            )}
            {badge && (
              <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] text-success-foreground">
                {badge}
              </span>
            )}
            <span className="w-24 shrink-0 text-right text-[11px] text-muted-foreground/70">
              {formatRelativeTime(thread.updatedAt || thread.createdAt)}
            </span>
            <StatusDot
              attention={thread.status === "finished" && !thread.viewed}
            />
          </Link>
        </ContextMenu.Trigger>
        <ContextMenu.Portal>
          <ContextMenu.Positioner className="z-50 outline-none">
            <ContextMenu.Popup className={MENU_POPUP_CLASS}>
              {thread.traceUrl && (
                <ContextMenu.LinkItem
                  href={thread.traceUrl}
                  target="_blank"
                  rel="noreferrer"
                  closeOnClick
                  className={MENU_ITEM_CLASS}
                >
                  <TreeStructureIcon className="size-3.5" />
                  Open trace
                </ContextMenu.LinkItem>
              )}
              {thread.sourceUrl && (
                <ContextMenu.LinkItem
                  href={thread.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  closeOnClick
                  className={MENU_ITEM_CLASS}
                >
                  <IoLogoSlack className="size-3.5" />
                  Open Slack thread
                </ContextMenu.LinkItem>
              )}
              <ContextMenu.Item
                disabled={!thread.sandboxId}
                title={thread.sandboxId ?? undefined}
                onClick={() => {
                  if (!thread.sandboxId) return
                  void navigator.clipboard.writeText(thread.sandboxId)
                }}
                className={MENU_ITEM_CLASS}
              >
                <CopyIcon className="size-3.5" />
                Copy sandbox ID
              </ContextMenu.Item>
              {!isReadOnly && (
                <ContextMenu.Item
                  disabled={resolveThread.isPending}
                  onClick={() =>
                    resolveThread.mutate({
                      threadId: thread.id,
                      resolved: !isResolved,
                    })
                  }
                  className={MENU_ITEM_CLASS}
                >
                  {isResolved ? (
                    <ArrowCounterClockwiseIcon className="size-3.5" />
                  ) : (
                    <CheckCircleIcon className="size-3.5" />
                  )}
                  {isResolved ? "Unresolve thread" : "Resolve thread"}
                </ContextMenu.Item>
              )}
              {!isReadOnly && (
                <ContextMenu.Item
                  disabled={isDeleting}
                  onClick={() => setDeleteOpen(true)}
                  className={cn(MENU_ITEM_CLASS, "text-destructive")}
                >
                  <TrashIcon className="size-3.5" />
                  Delete thread
                </ContextMenu.Item>
              )}
            </ContextMenu.Popup>
          </ContextMenu.Positioner>
        </ContextMenu.Portal>
      </ContextMenu.Root>
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={thread.title}
        isDeleting={isDeleting}
        onConfirm={() => {
          if (isDeleting) return
          deleteThread.mutate(thread.id, {
            onSuccess: () => setDeleteOpen(false),
          })
        }}
      />
    </>
  )
}

function LocalSessionRow({
  session,
  projectLabel,
  running,
  onDelete,
}: {
  session: DesktopLocalThreadSummary
  projectLabel: string
  running: boolean
  onDelete: (sessionId: string) => Promise<boolean>
}) {
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)

  const confirmDelete = async () => {
    if (isDeleting) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      if (!(await onDelete(session.id))) {
        throw new Error("Local Open SWE thread not found")
      }
      setDeleteOpen(false)
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
      <ContextMenu.Root onOpenChange={setContextMenuOpen}>
        <ContextMenu.Trigger
          className={cn("group relative", isDeleting && "opacity-50")}
        >
          <Link
            to="/agents/local/$sessionId"
            params={{ sessionId: session.id }}
            onClick={(event: React.MouseEvent) => {
              if (contextMenuOpen) event.preventDefault()
            }}
            className={ROW_CLASS}
          >
            {running ? (
              <CircleNotchIcon
                className="size-4 shrink-0 animate-spin text-primary"
                aria-label="Session running"
              />
            ) : (
              <DesktopIcon className="size-4 shrink-0 text-muted-foreground/80">
                <title>Session on this computer</title>
              </DesktopIcon>
            )}
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-[13px] text-foreground">
                {session.title}
              </span>
              <span className="truncate text-[11px] text-muted-foreground/70">
                {projectLabel}
              </span>
            </div>
            <span className="w-24 shrink-0 text-right text-[11px] text-muted-foreground/70">
              {formatRelativeTime(session.updatedAt || session.createdAt)}
            </span>
            <StatusDot attention={!session.viewed && !running} />
          </Link>
        </ContextMenu.Trigger>
        <ContextMenu.Portal>
          <ContextMenu.Positioner className="z-50 outline-none">
            <ContextMenu.Popup className={MENU_POPUP_CLASS}>
              <ContextMenu.Item
                disabled={isDeleting}
                onClick={() => setDeleteOpen(true)}
                className={cn(MENU_ITEM_CLASS, "text-destructive")}
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
        threadTitle={session.title}
        isDeleting={isDeleting}
        onConfirm={() => void confirmDelete()}
        detail="This removes its history but does not revert changes made to your project."
        error={deleteError}
      />
    </>
  )
}
