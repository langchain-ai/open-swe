import { type ReactNode, useRef, useState } from "react"
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
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"

import { useNavigate } from "@tanstack/react-router"
import type { DesktopLegacyLocalThread } from "@/desktop"
import { useLocalThread } from "@/features/agents/lib/desktopLocal"
import { useRefreshLegacyLocalThreads } from "@/features/agents/lib/legacyLocal"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"

import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import {
  ThreadMenuItems,
  type ThreadMenuKind,
} from "@/features/agents/components/ThreadMenuItems"
import { ThreadParticipants } from "@/features/agents/components/ThreadParticipants"
import { ThreadVisibilityMenu } from "@/features/agents/components/ThreadVisibilityMenu"
import { ShareThreadDialog } from "@/features/agents/components/ShareThreadDialog"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  useContinueThreadPrivately,
  useDeleteAgentThread,
  usePinAgentThread,
  useResolveAgentThread,
  useShareThreadWithWorkspace,
  useSidebarPinnedThreads,
  useSidebarRepos,
} from "@/features/agents/lib/queries"
import type { ThreadVisibility } from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

function ThreadRepoIndicator({
  thread,
  localThread,
}: {
  thread?: AgentThread
  localThread?: DesktopLegacyLocalThread
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
    <Tooltip title={repoName} side="bottom" open={open} onOpenChange={setOpen}>
      <button
        type="button"
        // Default-prevented so the trigger's own click handler keeps it open.
        onClick={(event) => {
          event.preventDefault()
          setOpen(true)
        }}
        onPointerLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        aria-label={`Repository: ${repoName}`}
        data-no-drag=""
        className="flex size-7 shrink-0 items-center justify-center text-icon-secondary"
      >
        <FolderIcon size={16} weight="regular" />
      </button>
    </Tooltip>
  )
}

export function AgentThreadHeader({
  title,
  target,
  targetMenu,
  panelCollapsed,
  thread,
  onRename,
  localThread,
  visibility,
  onVisibilityChange,
}: {
  title?: string | null
  target: "Cloud" | "This Mac" | "Local CLI"
  /** Replaces the target label with a control that moves the thread. */
  targetMenu?: ReactNode
  panelCollapsed: boolean
  onRename?: (title: string) => Promise<unknown>
  localThread?: DesktopLegacyLocalThread
  thread?: AgentThread
  // Visibility of a thread that does not exist yet.
  visibility?: ThreadVisibility
  onVisibilityChange?: (next: ThreadVisibility) => void
}) {
  const navigate = useNavigate()
  const refreshLocalThreads = useRefreshLegacyLocalThreads()
  const { prefs, toggleLocalPin } = useSidebarPrefs()
  const [deletingLocal, setDeletingLocal] = useState(false)
  const worktreeThread = useLocalThread(thread?.id ?? "") ?? localThread
  const sidebarCollapsed = useSidebarCollapsed()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const pinnedThreads = useSidebarPinnedThreads({ enabled: Boolean(thread) })
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const deleteThread = useDeleteAgentThread()
  const continuePrivately = useContinueThreadPrivately()
  const shareWithWorkspace = useShareThreadWithWorkspace()
  const session = useSession()
  // A "This Mac" thread runs commands on its owner's machine, so it stays private.
  const canShare = Boolean(
    thread?.ownerLogin &&
    thread.ownerLogin.toLowerCase() === session.data?.login.toLowerCase() &&
    thread.sandboxBridgeClient !== "desktop"
  )
  const [shareOpen, setShareOpen] = useState(false)
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
    try {
      const deleted = await window.openSweDesktop?.deleteLegacyLocalThread(
        localThread.id
      )
      if (!deleted) throw new Error("Local Open SWE thread not found")
      refreshLocalThreads(localThread.id)
      setDeleteOpen(false)
      void navigate({ to: "/agents" })
    } catch (error) {
      reportError({ title: "Couldn't delete thread", error })
    }
    setDeletingLocal(false)
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
    try {
      await onRename(next)
    } catch (error) {
      // Cloud renames go through a mutation, which reports its own failure.
      if (localThread) reportError({ title: "Couldn't rename thread", error })
    }
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
    if (!thread || localThread || continuePrivately.isPending) return
    continuePrivately.mutate(thread.id)
  }
  const visibilityMenu = thread ? (
    <ThreadVisibilityMenu
      value={thread.visibility ?? "public"}
      onChange={(next) => {
        if (next === "private") continueThreadPrivately()
        else if (canShare) setShareOpen(true)
      }}
      disabledValues={
        thread.visibility === "private" && !canShare ? ["public"] : []
      }
      busy={continuePrivately.isPending || shareWithWorkspace.isPending}
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
  const menuItems = (menu: ThreadMenuKind) => (
    <ThreadMenuItems
      menu={menu}
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
            ?.updateLegacyLocalThread({
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
      className="relative z-10 h-11 shrink-0 border-b border-subtle bg-surface-level-1/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-surface-level-1/60 after:to-transparent"
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
              <input
                autoFocus
                onFocus={(event) => event.currentTarget.select()}
                aria-label="Thread title"
                data-no-drag=""
                className="min-w-0 rounded-md bg-surface-level-2 px-2 py-1 outline-none focus-visible:ring-2 focus-visible:ring-focus"
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
                aria-busy={savingTitle !== null}
                disabled={savingTitle !== null}
                title={savingTitle ?? title}
                data-no-drag=""
                className="min-w-0 truncate rounded-md px-2 py-1 text-left transition-colors hover:bg-surface-level-2 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
                onClick={startRename}
              >
                {savingTitle ?? title}
              </button>
            ) : (
              <span className="min-w-0 truncate" title={title}>
                {title}
              </span>
            )}
            {(thread || localThread) && (
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
                  className="min-w-[10rem]"
                >
                  {menuItems("dropdown")}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-3">
          {thread && !localThread && (
            <ThreadParticipants threadId={thread.id} />
          )}
          {targetMenu ?? (
            <span className="text-xs text-secondary">{target}</span>
          )}
          {!localThread && visibilityMenu}
        </div>
      </div>
    </header>
  )

  if (!thread && !localThread) return header

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{header}</ContextMenuTrigger>
        <ContextMenuContent
          onCloseAutoFocus={keepRenameFocus}
          className="min-w-[10rem]"
        >
          {menuItems("context")}
        </ContextMenuContent>
      </ContextMenu>
      {thread && (
        <ShareThreadDialog
          open={shareOpen}
          onOpenChange={setShareOpen}
          busy={shareWithWorkspace.isPending}
          running={thread.status === "running"}
          onConfirm={() =>
            shareWithWorkspace.mutate(thread.id, {
              onSuccess: () => setShareOpen(false),
            })
          }
        />
      )}
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={title ?? ""}
        isDeleting={isDeleting}
        onConfirm={() => void confirmDelete()}
        detail={
          worktreeThread
            ? worktreeThread.ownedWorktrees?.length
              ? "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
              : "This removes its history but does not revert changes made to your repository."
            : undefined
        }
      />
    </>
  )
}
