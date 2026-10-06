import { useRef, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { PageBand } from "@langchain/gtm-platform-design-system/patterns/page-band"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/context-menu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"

import type { DesktopLegacyLocalThread } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import type { ThreadVisibility } from "@/lib/api"
import type { Glyph } from "@/components/glyphs"
import { Cloud, Ellipsis, Folder, Monitor, Terminal } from "@/components/glyphs"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { ShareThreadDialog } from "@/features/agents/components/ShareThreadDialog"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { ThreadVisibilityMenu } from "@/features/agents/components/ThreadVisibilityMenu"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import { useLocalThread } from "@/features/agents/lib/desktopLocal"
import { useRefreshLegacyLocalThreads } from "@/features/agents/lib/legacyLocal"
import {
  useContinueThreadPrivately,
  useDeleteAgentThread,
  usePinAgentThread,
  useResolveAgentThread,
  useShareThreadWithWorkspace,
  useSidebarPinnedThreads,
  useSidebarRepos,
} from "@/features/agents/lib/queries"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { reportError } from "@/lib/errorReporting"
import { useSession } from "@/lib/session"

type ThreadTarget = "Cloud" | "This Mac" | "Local CLI"

const TARGET_GLYPH: Record<ThreadTarget, Glyph> = {
  Cloud: Cloud,
  "This Mac": Monitor,
  "Local CLI": Terminal,
}

const WORKTREE_DELETE_DETAIL =
  "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
const LOCAL_DELETE_DETAIL =
  "This removes its history but does not revert changes made to your repository."

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
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Repository: ${repoName}`}
            data-no-drag=""
            className="text-ink-subtle"
          />
        }
        closeOnClick={false}
        onClick={() => setOpen(true)}
        onPointerLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
      >
        <Icon icon={Folder} />
      </TooltipTrigger>
      <TooltipContent side="bottom">{repoName}</TooltipContent>
    </Tooltip>
  )
}

/**
 * The thread's focus band: one 44px toolbar carrying the title, the object's
 * actions, where it runs and who can read it. It is the only band on the route.
 */
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
  target: ThreadTarget
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
    if (!localThread) {
      if (thread) await deleteThread.mutateAsync(thread.id)
      return
    }
    setDeletingLocal(true)
    try {
      const deleted = await window.openSweDesktop?.deleteLegacyLocalThread(
        localThread.id
      )
      if (!deleted) throw new Error("Local Open SWE thread not found")
      refreshLocalThreads(localThread.id)
      void navigate({ to: "/agents" })
    } finally {
      setDeletingLocal(false)
    }
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
            ?.updateLegacyLocalThread({
              threadId: localThread.id,
              archived: !archived,
            })
            .then(() => refreshLocalThreads(localThread.id))
            .catch((error: unknown) =>
              reportError({ title: "Couldn't archive or restore thread", error })
            )
        } else if (thread && !resolveThread.isPending) {
          resolveThread.mutate({ threadId: thread.id, resolved: !archived })
        }
      }}
      onDelete={() => setDeleteOpen(true)}
    />
  )
  // Returning focus to the menu trigger would pull it out of the title editor.
  const keepEditorFocus = () => (editingRef.current ? false : undefined)

  const header = (
    <Box
      render={<header />}
      data-desktop-drag-region=""
      className={cn(
        "shrink-0",
        // The macOS traffic lights overhang the 48px icon rail.
        isDesktop && "in-data-[rail-collapsed=true]:pl-6",
        // Clears the fixed control that reopens the collapsed right panel.
        panelCollapsed && "pr-8"
      )}
    >
      <PageBand variant="toolbar" edge="none">
        <Inline gap="xs" align="center" grow className="min-w-0">
          {title && (thread || localThread) && (
            <ThreadRepoIndicator thread={thread} localThread={localThread} />
          )}
          {title &&
            (draft !== null ? (
              <Input
                autoFocus
                onFocus={(event) => event.currentTarget.select()}
                aria-label="Thread title"
                data-no-drag=""
                className="font-medium"
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
              <Button
                ref={titleButtonRef}
                variant="ghost"
                aria-label="Rename thread"
                aria-busy={savingTitle !== null}
                disabled={savingTitle !== null}
                title={savingTitle ?? title}
                data-no-drag=""
                className="min-w-0 justify-start px-2"
                onClick={startRename}
              >
                <Box render={<span />} className="truncate">
                  {savingTitle ?? title}
                </Box>
              </Button>
            ) : (
              <Box
                render={<span />}
                title={title}
                className="min-w-0 truncate px-2 font-medium text-ink"
              >
                {title}
              </Box>
            ))}
          {title && (thread || localThread) && (
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Thread actions"
                    data-no-drag=""
                    className="text-ink-subtle"
                  />
                }
              >
                <Icon icon={Ellipsis} />
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="start"
                className="min-w-48"
                finalFocus={keepEditorFocus}
              >
                {menuItems}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </Inline>
        <Inline gap="md" align="center" className="shrink-0">
          <Inline
            gap="xs"
            align="center"
            ink="ink-subtle"
            className="text-meta"
          >
            <Icon icon={TARGET_GLYPH[target]} size="sm" />
            {target}
          </Inline>
          {!localThread && visibilityMenu}
        </Inline>
      </PageBand>
    </Box>
  )

  if (!thread && !localThread) return header

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger render={header} />
        <ContextMenuContent className="min-w-48" finalFocus={keepEditorFocus}>
          {menuItems}
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
      {deleteOpen && (
        <DeleteThreadDialog
          threadTitle={title ?? ""}
          onConfirm={confirmDelete}
          onDismiss={() => setDeleteOpen(false)}
          detail={
            worktreeThread
              ? worktreeThread.ownedWorktrees?.length
                ? WORKTREE_DELETE_DETAIL
                : LOCAL_DELETE_DETAIL
              : undefined
          }
        />
      )}
    </>
  )
}
