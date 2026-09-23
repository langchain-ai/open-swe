import { DotsThreeIcon } from "@phosphor-icons/react"
import { Folder } from "lucide-react"
import { useRef, useState } from "react"

import { useNavigate } from "@tanstack/react-router"
import type { DesktopLocalThreadSummary } from "@/desktop"
import { useRefreshLocalThreads } from "@/features/agents/lib/desktopLocal"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"

import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { IconButton } from "@/components/ui/button"
import {
  ContextMenu,
  ContextMenuPopup,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { Input } from "@/components/ui/input"
import { Menu, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { ThreadVisibilityMenu } from "@/features/agents/components/ThreadVisibilityMenu"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  useContinueThreadPrivately,
  useDeleteAgentThread,
  usePinAgentThread,
  useResolveAgentThread,
  useSidebarPinnedThreads,
  useSidebarRepos,
} from "@/features/agents/lib/queries"
import type { ThreadVisibility } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

function ThreadRepoIndicator({
  thread,
  localThread,
}: {
  thread?: AgentThread
  localThread?: DesktopLocalThreadSummary
}) {
  const [open, setOpen] = useState(false)
  const { projects: localRepos } = useDesktopProjects()
  const { prefs } = useSidebarPrefs()
  const session = useSession()
  const cloudRepos = useSidebarRepos({
    ...prefs.filters,
    enabled: !localThread && Boolean(session.data),
  })
  const repo = !localThread ? thread?.repoFullName.trim() : undefined
  const repoName = localThread
    ? localRepos.find((candidate) => candidate.cwd === localThread.cwd)?.name
    : (cloudRepos.data?.find(
        (candidate) =>
          candidate.repoFullName.toLowerCase() === repo?.toLowerCase()
      )?.name ?? (repo ? thread?.repo || repo : undefined))
  if (!repoName) return null

  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        render={
          <IconButton
            variant="ghost"
            data-no-drag=""
            className="text-muted-foreground"
          />
        }
        closeOnClick={false}
        onClick={() => setOpen(true)}
        onPointerLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        aria-label={`Repository: ${repoName}`}
      >
        <Folder className="size-4" />
      </TooltipTrigger>
      <TooltipPopup>{repoName}</TooltipPopup>
    </Tooltip>
  )
}

export function AgentThreadHeader({
  title,
  target,
  panelCollapsed,
  thread,
  onRename,
  localThread,
  visibility,
  onVisibilityChange,
}: {
  title?: string | null
  target: "Cloud" | "This Mac"
  panelCollapsed: boolean
  onRename?: (title: string) => Promise<unknown>
  localThread?: DesktopLocalThreadSummary
  thread?: AgentThread
  // Visibility of a thread that does not exist yet; existing threads read it
  // from `thread` and can only continue privately.
  visibility?: ThreadVisibility
  onVisibilityChange?: (next: ThreadVisibility) => void
}) {
  const navigate = useNavigate()
  const refreshLocalThreads = useRefreshLocalThreads()
  const { prefs, toggleLocalPin } = useSidebarPrefs()
  const [deletingLocal, setDeletingLocal] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const sidebarCollapsed = useSidebarCollapsed()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const pinnedThreads = useSidebarPinnedThreads({ enabled: Boolean(thread) })
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const deleteThread = useDeleteAgentThread()
  const continuePrivately = useContinueThreadPrivately()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const pinned = localThread
    ? prefs.pinnedLocalIds.includes(localThread.id)
    : Boolean(
        pinnedThreads.data?.some((candidate) => candidate.id === thread?.id)
      )
  const archived = localThread
    ? localThread.archived === true
    : thread?.resolved === true
  const isDeleting = deletingLocal || deleteThread.isPending
  const confirmDelete = async () => {
    if (isDeleting) return
    if (!localThread) {
      if (thread)
        deleteThread.mutate(thread.id, {
          onSuccess: () => setDeleteOpen(false),
        })
      return
    }
    setDeletingLocal(true)
    setDeleteError(null)
    try {
      const deleted = await window.openSweDesktop?.deleteLocalThread(
        localThread.id
      )
      if (deleted) {
        refreshLocalThreads(localThread.id)
        setDeleteOpen(false)
        void navigate({ to: "/agents" })
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
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [renameError, setRenameError] = useState<string | null>(null)
  const editingRef = useRef(false)
  const titleButtonRef = useRef<HTMLButtonElement>(null)
  const [editorWidth, setEditorWidth] = useState<number>()
  const saveTitle = async () => {
    if (!editingRef.current || !onRename || draft === null) return
    editingRef.current = false
    setDraft(null)
    const next = draft.trim()
    if (!next || next === title) return
    setSaving(true)
    try {
      await onRename(next)
    } catch (error) {
      setRenameError(
        error instanceof Error ? error.message : "Could not rename thread"
      )
    }
    setSaving(false)
  }

  const startRename = () => {
    if (!onRename || saving || !title) return
    setRenameError(null)
    setEditorWidth(titleButtonRef.current?.getBoundingClientRect().width)
    editingRef.current = true
    setDraft(title)
  }
  const continueThreadPrivately = () => {
    if (!thread || localThread || continuePrivately.isPending) return
    setRenameError(null)
    continuePrivately.mutate(thread.id, {
      onError: (error) =>
        setRenameError(
          error instanceof Error
            ? error.message
            : "Could not continue privately"
        ),
    })
  }
  const visibilityMenu = thread ? (
    <ThreadVisibilityMenu
      value={thread.visibility ?? "public"}
      onChange={(next) => {
        if (next === "private") continueThreadPrivately()
      }}
      disabledValues={thread.visibility === "private" ? ["public"] : []}
      busy={continuePrivately.isPending}
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
  const menuItems = (
    <ThreadMenuItems
      thread={thread ?? null}
      localThread={localThread}
      pinned={pinned}
      archived={archived}
      isDeleting={isDeleting}
      onTogglePin={() => {
        if (localThread) toggleLocalPin(localThread.id)
        else if (thread && !pinThread.isPending) {
          pinThread.mutate({ threadId: thread.id, pinned: !pinned })
        }
      }}
      onToggleArchived={() => {
        if (localThread) {
          void window.openSweDesktop
            ?.updateLocalThread({
              threadId: localThread.id,
              archived: !archived,
            })
            .then(() => refreshLocalThreads(localThread.id))
        } else if (thread && !resolveThread.isPending) {
          resolveThread.mutate({ threadId: thread.id, resolved: !archived })
        }
      }}
      onDelete={() => setDeleteOpen(true)}
    />
  )

  const header = (
    <header
      data-desktop-drag-region=""
      className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent"
    >
      <div
        className={cn(
          "flex h-full w-full items-center gap-3 px-4",
          sidebarCollapsed && (isDesktop ? "pl-32" : "pl-14"),
          panelCollapsed && "pr-14"
        )}
      >
        {title && (
          <div className="flex min-w-0 items-center gap-1 text-sm font-medium">
            {(thread || localThread) && (
              <ThreadRepoIndicator thread={thread} localThread={localThread} />
            )}
            {draft !== null ? (
              <Input
                autoFocus
                onFocus={(event) => event.currentTarget.select()}
                aria-label="Thread title"
                data-no-drag=""
                className="w-auto md:text-sm"
                style={{ width: editorWidth }}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
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
            ) : onRename ? (
              <button
                type="button"
                aria-label="Rename thread"
                ref={titleButtonRef}
                aria-busy={saving}
                disabled={saving}
                title={title}
                data-no-drag=""
                className="min-w-0 truncate rounded-md px-2 py-1 text-left transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-60"
                onClick={startRename}
              >
                {title}
              </button>
            ) : (
              <span className="min-w-0 truncate" title={title}>
                {title}
              </span>
            )}
            {(thread || localThread) && (
              <Menu>
                <MenuTrigger
                  render={
                    <IconButton
                      variant="ghost"
                      aria-label="Thread actions"
                      data-no-drag=""
                      className="text-muted-foreground"
                    />
                  }
                >
                  <DotsThreeIcon className="size-5" weight="bold" />
                </MenuTrigger>
                <MenuPopup
                  align="start"
                  className="min-w-40"
                  finalFocus={() => (editingRef.current ? false : undefined)}
                >
                  {menuItems}
                </MenuPopup>
              </Menu>
            )}
            {renameError && (
              <span
                role="alert"
                className="absolute top-full left-4 rounded-md border border-destructive/30 bg-background px-2 py-1 text-xs text-destructive"
              >
                {renameError}
              </span>
            )}
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-3">
          <span className="text-xs text-muted-foreground">{target}</span>
          {!localThread && visibilityMenu}
        </div>
      </div>
    </header>
  )

  if (!thread && !localThread) return header

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger render={header} />
        <ContextMenuPopup
          finalFocus={() => (editingRef.current ? false : undefined)}
        >
          {menuItems}
        </ContextMenuPopup>
      </ContextMenu>
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open)
          if (!open) setDeleteError(null)
        }}
        threadTitle={title ?? ""}
        isDeleting={isDeleting}
        onConfirm={() => void confirmDelete()}
        detail={
          localThread
            ? localThread.ownedWorktrees?.length
              ? "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
              : "This removes its history but does not revert changes made to your repository."
            : undefined
        }
        error={deleteError}
      />
    </>
  )
}
