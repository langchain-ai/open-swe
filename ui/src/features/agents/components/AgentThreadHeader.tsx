import { Input } from "@langchain/macaw-components/Input"
import { Button } from "@langchain/macaw-components/Button"
import { LaptopRegularIcon } from "@langchain/macaw-components/icons"
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
import { Tooltip } from "@langchain/macaw-components/Tooltip"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { TerminalIcon } from "@phosphor-icons/react/dist/ssr/Terminal"

import { useLocalThread } from "@/features/agents/lib/desktopLocal"
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
  usePinAgentThread,
  useResolveAgentThread,
  useShareThreadWithWorkspace,
  useSidebarPinnedThreads,
  useSidebarRepos,
} from "@/features/agents/lib/queries"
import type { ThreadVisibility } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

function ThreadRepoIndicator({ thread }: { thread: AgentThread }) {
  const [open, setOpen] = useState(false)
  const { prefs } = useSidebarPrefs()
  const session = useSession()
  const cloudRepos = useSidebarRepos({
    ...prefs.filters,
    enabled: Boolean(session.data),
  })
  const repo = thread.repoFullName.trim()
  const repoName =
    cloudRepos.data?.find(
      (candidate) => candidate.repoFullName.toLowerCase() === repo.toLowerCase()
    )?.name ?? (repo ? thread.repo || repo : undefined)
  if (!repoName) return null

  return (
    <Tooltip title={repoName} side="bottom" open={open} onOpenChange={setOpen}>
      <IconButton
        color="secondary"
        variant="plain"
        size="sm"
        icon={FolderIcon}
        iconWeight="regular"
        label={`Repository: ${repoName}`}
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

const TARGET_ICONS = {
  Cloud: null,
  "This Mac": LaptopRegularIcon,
  "Local CLI": TerminalIcon,
}

export function AgentThreadHeader({
  title,
  target,
  panelCollapsed,
  thread,
  onRename,
  visibility,
  onVisibilityChange,
}: {
  title?: string | null
  target: keyof typeof TARGET_ICONS
  panelCollapsed: boolean
  onRename?: (title: string) => Promise<unknown>
  thread?: AgentThread
  // Visibility of a thread that does not exist yet.
  visibility?: ThreadVisibility
  onVisibilityChange?: (next: ThreadVisibility) => void
}) {
  const worktreeThread = useLocalThread(thread?.id ?? "")
  const sidebarCollapsed = useSidebarCollapsed()
  const TargetIcon = TARGET_ICONS[target]
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const pinnedThreads = useSidebarPinnedThreads({ enabled: Boolean(thread) })
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const deleteThread = useDeleteAgentThread()
  const continuePrivately = useContinueThreadPrivately()
  const shareWithWorkspace = useShareThreadWithWorkspace()
  const session = useSession()
  // A thread on its owner's machine runs commands there, so it stays private.
  const canShare = Boolean(
    thread?.ownerLogin &&
    thread.ownerLogin.toLowerCase() === session.data?.login.toLowerCase() &&
    !thread.sandboxBridgeClient
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
  const menuItems = (menu: ThreadMenuKind, current: AgentThread) => (
    <ThreadMenuItems
      menu={menu}
      thread={current}
      pinned={pinned}
      archived={archived}
      isDeleting={isDeleting}
      onTogglePin={() => {
        if (!pinThread.isPending) {
          pinThread.mutate({ threadId: current.id, pinned: !pinned })
        }
      }}
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
      className="relative z-pane-header h-11 shrink-0 border-b border-subtle bg-surface-level-1/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-surface-level-1/60 after:to-transparent"
    >
      <div
        className={cn(
          "flex h-full w-full items-center gap-space-3 px-space-4",
          sidebarCollapsed && (isDesktop ? "pl-32" : "pl-space-8"),
          panelCollapsed && "pr-space-8"
        )}
      >
        {title && (
          <div className="flex min-w-0 items-center gap-space-1 text-sm font-medium">
            {thread && <ThreadRepoIndicator thread={thread} />}
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
                className="h-auto min-w-0 shrink justify-start truncate px-space-2 py-space-1 text-left text-inherit hover:bg-surface-level-2 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none disabled:bg-transparent"
                onClick={startRename}
              >
                <span className="truncate">{savingTitle ?? title}</span>
              </Button>
            ) : (
              <span className="min-w-0 truncate" title={title}>
                {title}
              </span>
            )}
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
                  className="min-w-[10rem]"
                >
                  {menuItems("dropdown", thread)}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-space-3">
          <span
            role="img"
            aria-label={`Runs on ${target}`}
            title={target}
            hidden={!TargetIcon}
            className="text-icon-secondary"
          >
            {TargetIcon && <TargetIcon size={16} weight="regular" />}
          </span>
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
          className="min-w-[10rem]"
        >
          {menuItems("context", thread)}
        </ContextMenuContent>
      </ContextMenu>
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
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={title ?? ""}
        isDeleting={isDeleting}
        onConfirm={confirmDelete}
        detail={
          worktreeThread
            ? worktreeThread.ownedWorktrees.length
              ? "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
              : "This removes its history but does not revert changes made to your repository."
            : undefined
        }
      />
    </>
  )
}
