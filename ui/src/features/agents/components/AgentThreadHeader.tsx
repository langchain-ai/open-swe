import { Input } from "@langchain/macaw-components/Input"
import { Button } from "@langchain/macaw-components/Button"
import { useRef, useState } from "react"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@langchain/macaw-components/ContextMenu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@/components/Skeleton"
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"

import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"

import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import {
  ThreadMenuItems,
  type ThreadMenuKind,
} from "@/features/agents/components/ThreadMenuItems"
import { ThreadVisibilityMenu } from "@/features/agents/components/ThreadVisibilityMenu"
import { ShareThreadDialog } from "@/features/agents/components/ShareThreadDialog"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  useContinueThreadPrivately,
  useDeleteAgentThread,
  useSetAgentThreadViewed,
  usePinAgentThread,
  useResolveAgentThread,
  useShareThread,
  useSidebarPinnedThreads,
  useSidebarRepos,
} from "@/features/agents/lib/queries"
import { threadProject, useProjects } from "@/features/projects/lib/projects"
import { copyPageLink } from "@/features/reviews/lib/copyText"
import type { ThreadVisibility } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

/** The folder the sidebar files the thread under: its project, else its repository. */
function useThreadFolder(thread?: AgentThread) {
  const { prefs } = useSidebarPrefs()
  const cloudRepos = useSidebarRepos({
    ...prefs.filters,
    enabled: false,
  })
  const { projects } = useProjects()
  if (!thread) return undefined
  const project = threadProject(projects, thread)
  if (project) return { kind: "Project", name: project.name }
  const repo = thread.repoFullName.trim()
  const repoName =
    cloudRepos.data?.find(
      (candidate) =>
        !candidate.projectId &&
        candidate.repoFullName?.toLowerCase() === repo.toLowerCase()
    )?.name ?? (repo ? thread.repo || repo : undefined)
  return repoName ? { kind: "Repository", name: repoName } : undefined
}

function ThreadFolderIcon({ kind, name }: { kind: string; name: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Tooltip title={name} side="bottom" open={open} onOpenChange={setOpen}>
      <IconButton
        color="secondary"
        variant="plain"
        size="sm"
        icon={FolderIcon}
        iconWeight="regular"
        label={`${kind}: ${name}`}
        tooltipProps={{ disabled: true }}
        className="size-7 shrink-0"
        type="button"
        onClick={(event) => {
          event.preventDefault()
          setOpen(true)
        }}
        onPointerLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        data-no-drag=""
      />
    </Tooltip>
  )
}

export function AgentThreadHeader({
  panelCollapsed,
  thread,
  startingThread,
  onRename,
  visibility,
  onVisibilityChange,
  loading = false,
}: {
  panelCollapsed: boolean
  onRename?: (title: string) => Promise<unknown>
  thread?: AgentThread
  /** The thread is open but its summary, title included, is still on its way. */
  loading?: boolean
  /**
   * A thread whose first run is still being accepted. It is laid out as the
   * thread it is about to become, so nothing moves on arrival, but offers none
   * of a thread's actions yet.
   */
  startingThread?: AgentThread
  // Visibility of a thread that does not exist yet.
  visibility?: ThreadVisibility
  onVisibilityChange?: (next: ThreadVisibility) => void
}) {
  const shownThread = thread ?? startingThread
  const title = shownThread?.title
  const folder = useThreadFolder(shownThread)
  const sidebarCollapsed = useSidebarCollapsed()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const pinnedThreads = useSidebarPinnedThreads({
    enabled: Boolean(thread),
    refetchOnMount: false,
  })
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const deleteThread = useDeleteAgentThread()
  const setViewed = useSetAgentThreadViewed()
  const continuePrivately = useContinueThreadPrivately()
  const shareThread = useShareThread()
  const session = useSession()
  const canShare = Boolean(
    thread?.ownerLogin &&
    thread.ownerLogin.toLowerCase() === session.data?.login.toLowerCase()
  )
  const [shareOpen, setShareOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const pinned = Boolean(
    pinnedThreads.data?.some((candidate) => candidate.id === thread?.id)
  )
  const archived = thread?.resolved === true
  const isDeleting = deleteThread.isPending
  const confirmDelete = () => {
    if (isDeleting || !thread) return
    deleteThread.mutate(thread.id, { onSuccess: () => setDeleteOpen(false) })
  }
  const [draft, setDraft] = useState<string | null>(null)
  const [savingTitle, setSavingTitle] = useState<string | null>(null)
  const editingRef = useRef(false)
  const titleButtonRef = useRef<HTMLButtonElement>(null)
  const [editorWidth, setEditorWidth] = useState<number>()
  const saveTitle = async () => {
    if (!editingRef.current || !onRename || draft === null) return
    editingRef.current = false
    setDraft(null)
    const next = draft.trim()
    if (!next || next === title) return
    setSavingTitle(next)
    // Renames go through a mutation, which reports its own failure.
    await onRename(next).catch(() => undefined)
    setSavingTitle(null)
  }

  const startRename = () => {
    if (!onRename || savingTitle !== null || !title) return
    setEditorWidth(titleButtonRef.current?.getBoundingClientRect().width)
    editingRef.current = true
    setDraft(title)
  }
  // A rename started from the menu owns focus; don't hand it back to the trigger.
  const keepRenameFocus = (event: Event) => {
    if (editingRef.current) event.preventDefault()
  }
  const continueThreadPrivately = () => {
    if (!thread || continuePrivately.isPending) return
    continuePrivately.mutate(thread.id)
  }
  const visibilityMenu = thread ? (
    <ThreadVisibilityMenu
      value={thread.visibility ?? "public"}
      onCopyLink={thread.visibility === "private" ? undefined : copyPageLink}
      onChange={(next) => {
        if (next === "private") continueThreadPrivately()
        else if (canShare) setShareOpen(true)
      }}
      disabledValues={
        thread.visibility === "private" && !canShare ? ["public"] : []
      }
      busy={continuePrivately.isPending || shareThread.isPending}
    />
  ) : visibility ? (
    <ThreadVisibilityMenu
      value={visibility}
      onChange={(next) => onVisibilityChange?.(next)}
      disabledValues={
        onVisibilityChange
          ? []
          : [visibility === "private" ? "public" : "private"]
      }
    />
  ) : null
  const menuItems = (menu: ThreadMenuKind, current: AgentThread) => (
    <ThreadMenuItems
      menu={menu}
      thread={current}
      pinned={pinned}
      archived={archived}
      isDeleting={isDeleting}
      onRename={
        onRename && savingTitle === null
          ? () => {
              editingRef.current = true
              requestAnimationFrame(startRename)
            }
          : undefined
      }
      onTogglePin={() => {
        if (!pinThread.isPending) {
          pinThread.mutate({ threadId: current.id, pinned: !pinned })
        }
      }}
      onToggleViewed={() =>
        setViewed.mutate({ threadId: current.id, viewed: !current.viewed })
      }
      onToggleArchived={() => {
        if (!resolveThread.isPending) {
          resolveThread.mutate({ threadId: current.id, resolved: !archived })
        }
      }}
      onDelete={() => setDeleteOpen(true)}
    />
  )

  const header = (
    <header
      data-desktop-drag-region=""
      className="relative z-pane-header h-11 shrink-0 border-b border-muted bg-surface-level-1/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-surface-level-1/60 after:to-transparent"
    >
      <div
        className={cn(
          "@container flex h-full w-full items-center gap-space-3 px-space-4",
          sidebarCollapsed && (isDesktop ? "pl-48" : "pl-space-8"),
          panelCollapsed && "pr-space-8"
        )}
      >
        {!title && loading && <Skeleton className="ml-space-2 h-4 w-40" />}
        {title && (
          <div className="flex min-w-0 items-center gap-space-1 text-sm font-medium">
            {folder && <ThreadFolderIcon {...folder} />}
            {/* The folder leads the title as a breadcrumb, or sits above it
                where the header is too narrow for both on one line. */}
            <div
              className={cn(
                "flex min-w-0 items-center",
                folder &&
                  draft === null &&
                  "flex-col items-start @xl:flex-row @xl:items-center"
              )}
            >
              {folder && draft === null && (
                <span
                  aria-hidden="true"
                  title={folder.name}
                  className="flex max-w-full min-w-0 shrink-[2] cursor-default items-center gap-space-2 px-space-2 text-xs leading-4 text-tertiary select-none @xl:max-w-60 @xl:pr-space-1 @xl:text-sm @xl:leading-normal"
                >
                  <span className="truncate">{folder.name}</span>
                  <span className="hidden font-normal text-quaternary @xl:inline">
                    /
                  </span>
                </span>
              )}
              {draft !== null ? (
                <div className="min-w-0" style={{ width: editorWidth }}>
                  <Input
                    size="md"
                    debounceMs={0}
                    autoFocus
                    onFocus={(event) => event.currentTarget.select()}
                    aria-label="Thread title"
                    data-no-drag=""
                    className="min-w-0"
                    inputContainerClassName="bg-surface-level-2 border-strong/70 [&:hover:not(:focus-within)]:border-strong"
                    value={draft}
                    onChange={setDraft}
                    onBlur={() => void saveTitle()}
                    onKeyDown={(event) => {
                      if (event.nativeEvent.isComposing) return
                      if (event.key === "Enter") {
                        event.preventDefault()
                        void saveTitle()
                      } else if (event.key === "Escape") {
                        event.preventDefault()
                        editingRef.current = false
                        setDraft(null)
                      }
                    }}
                  />
                </div>
              ) : onRename ? (
                <Button
                  color="secondary"
                  variant="plain"
                  size="md"
                  type="button"
                  aria-label="Rename thread"
                  ref={titleButtonRef}
                  aria-busy={savingTitle !== null}
                  disabled={savingTitle !== null}
                  title={savingTitle ?? title}
                  data-no-drag=""
                  className={cn(
                    "h-auto max-w-full min-w-0 shrink justify-start truncate px-space-2 py-space-1 text-left text-inherit hover:bg-surface-level-2 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none disabled:bg-transparent",
                    folder && "@max-xl:py-0"
                  )}
                  onClick={startRename}
                >
                  <span className="truncate">{savingTitle ?? title}</span>
                </Button>
              ) : (
                // Boxed and set like the rename button (Macaw's `Button` tightens
                // tracking and leading), so the title reads the same once the
                // thread can be renamed.
                <span
                  className={cn(
                    "max-w-full min-w-0 truncate border border-transparent px-space-2 py-space-1 leading-[1.15] tracking-tighter",
                    folder && "@max-xl:py-0"
                  )}
                  title={title}
                >
                  {title}
                </span>
              )}
            </div>
            {thread && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <IconButton
                    icon={DotsThreeIcon}
                    iconWeight="bold"
                    label="Thread actions"
                    size="sm"
                    color="secondary"
                    variant="plain"
                    data-no-drag=""
                  />
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  sideOffset={4}
                  onCloseAutoFocus={keepRenameFocus}
                  className="min-w-[10rem] border-default bg-elevated"
                >
                  {menuItems("dropdown", thread)}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-space-3">
          {visibilityMenu}
        </div>
      </div>
    </header>
  )

  if (!thread) return header

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{header}</ContextMenuTrigger>
        <ContextMenuContent
          onCloseAutoFocus={keepRenameFocus}
          className="min-w-[10rem] border-default bg-elevated"
        >
          {menuItems("context", thread)}
        </ContextMenuContent>
      </ContextMenu>
      <ShareThreadDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        busy={shareThread.isPending}
        running={thread.status === "running"}
        onConfirm={() =>
          shareThread.mutate(thread.id, {
            onSuccess: () => setShareOpen(false),
          })
        }
      />
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        isDeleting={isDeleting}
        onConfirm={confirmDelete}
      />
    </>
  )
}
