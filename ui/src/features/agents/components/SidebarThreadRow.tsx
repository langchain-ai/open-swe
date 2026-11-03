import { useEffect, useRef, useState } from "react"
import { Link } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { DropdownMenuItem, DropdownMenuSubTrigger } from "@/components/MenuItem"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import type { AgentStatus } from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import {
  markAgentThreadViewed,
  markReviewViewed,
  useDeleteAgentThread,
  useRenameAgentThread,
} from "@/features/agents/lib/queries"
import {
  noteReviewOpenedFromSidebar,
  reviewPageRoute,
} from "@/features/reviews/lib/reviewEntry"
import {
  useAssignChatProject,
  useChatProjects,
  type ChatProject,
} from "@/features/agents/lib/chatProjects"
import { cn } from "@/lib/utils"

export function SidebarConversationRow({
  title,
  threadId,
  status,
  viewed,
  active,
  compact = false,
  onNavigate,
  pinned,
  archived,
  pending,
  onPin,
  onArchive,
  onDelete,
  projects = [],
  projectId,
  projectLabel,
  onMove,
  onMoveUp,
  onMoveDown,
  reviewPage,
}: {
  title: string
  threadId: string
  status: AgentStatus
  viewed: boolean
  active: boolean
  compact?: boolean
  onNavigate: () => void
  pinned: boolean
  archived: boolean
  pending: boolean
  onPin: () => void
  onArchive: () => void
  onDelete?: () => void
  projects?: ChatProject[]
  projectId?: string
  projectLabel?: string
  onMove?: (projectId: string | null) => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  reviewPage?: SidebarThreadItem["reviewPage"]
}) {
  const rename = useRenameAgentThread()
  const [draft, setDraft] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const editing = useRef(false)
  const busy = pending || rename.isPending
  const isEditing = draft !== null
  useEffect(() => {
    if (!isEditing) return
    const frame = requestAnimationFrame(() => input.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [isEditing])
  const save = () => {
    if (!editing.current || draft === null) return
    editing.current = false
    const next = draft.trim()
    setDraft(null)
    if (next && next !== title) rename.mutate({ threadId, title: next })
  }
  const content = (
    <>
      <span className="flex min-w-0 flex-1 flex-col" title={title}>
        <span className="truncate text-sm">{title}</span>
        {projectLabel && (
          <span className="truncate text-xxs text-tertiary">
            {projectLabel}
          </span>
        )}
      </span>
      <span className="flex size-3.5 shrink-0 items-center justify-center">
        {status === "error" ? (
          <WarningCircleIcon
            role="img"
            aria-label="Chat error"
            size={14}
            weight="regular"
            className="text-icon-error"
          />
        ) : status === "running" ? (
          <span role="img" aria-label="Chat running">
            <Spinner size="xs" />
          </span>
        ) : !viewed ? (
          <span
            role="img"
            aria-label="Unread chat"
            className="size-2 rounded-full bg-brand"
          />
        ) : null}
      </span>
    </>
  )
  const linkProps = {
    onClick: onNavigate,
    "aria-current": active ? ("page" as const) : undefined,
    className: cn(
      "flex min-w-0 items-center gap-space-2 rounded-lg pr-9 pl-space-2 text-secondary transition-colors hover:bg-surface-level-2-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
      projectLabel ? "h-11" : compact ? "h-7" : "h-8",
      active && "bg-selected text-primary"
    ),
  }
  return (
    <div className="group/row relative mb-0.5">
      {draft !== null ? (
        <Input
          ref={input}
          size="sm"
          debounceMs={0}
          aria-label="Chat title"
          value={draft}
          onChange={setDraft}
          onFocus={(event) => event.currentTarget.select()}
          onBlur={save}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return
            if (event.key === "Enter") {
              event.preventDefault()
              save()
            }
            if (event.key === "Escape") {
              editing.current = false
              setDraft(null)
            }
          }}
        />
      ) : (
        <>
          {reviewPage ? (
            <Link {...reviewPageRoute(reviewPage)} {...linkProps}>
              {content}
            </Link>
          ) : (
            <Link to="/agents/$threadId" params={{ threadId }} {...linkProps}>
              {content}
            </Link>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconButton
                icon={DotsThreeIcon}
                label={`Actions for ${title}`}
                size="xs"
                color="secondary"
                variant="plain"
                disabled={busy}
                className="absolute top-1/2 right-1 -translate-y-1/2 opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 data-[state=open]:opacity-100"
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              side="right"
              className="w-48"
              onCloseAutoFocus={(event) => {
                if (editing.current) event.preventDefault()
              }}
            >
              <DropdownMenuItem
                disabled={busy}
                onSelect={() => {
                  editing.current = true
                  setDraft(title)
                }}
              >
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem disabled={busy} onSelect={onPin}>
                {pinned ? "Unpin" : "Pin"}
              </DropdownMenuItem>
              {pinned && (
                <>
                  <DropdownMenuItem
                    disabled={busy || !onMoveUp}
                    onSelect={onMoveUp}
                  >
                    Move Up
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    disabled={busy || !onMoveDown}
                    onSelect={onMoveDown}
                  >
                    Move Down
                  </DropdownMenuItem>
                </>
              )}
              {onMove && (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger disabled={busy}>
                    Move to Project
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="max-h-72 w-48 overflow-y-auto">
                    <DropdownMenuItem
                      disabled={!projectId}
                      onSelect={() => onMove(null)}
                    >
                      No Project
                    </DropdownMenuItem>
                    {projects.map((project) => (
                      <DropdownMenuItem
                        key={project.id}
                        disabled={project.id === projectId}
                        onSelect={() => onMove(project.id)}
                      >
                        {project.name}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              )}
              <DropdownMenuItem disabled={busy} onSelect={onArchive}>
                {archived ? "Unarchive" : "Archive"}
              </DropdownMenuItem>
              {onDelete && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    disabled={busy}
                    className="text-error"
                    onSelect={onDelete}
                  >
                    Delete
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      )}
    </div>
  )
}

export function SidebarThreadRow({
  item,
  isActive,
  pinned,
  archived,
  compact,
  pending = false,
  projectLabel,
  onNavigate,
  onTogglePin,
  onToggleArchived,
  onMoveUp,
  onMoveDown,
  activeThreadId: _activeThreadId,
}: {
  item: SidebarThreadItem
  isActive: boolean
  activeThreadId?: string
  pinned: boolean
  archived: boolean
  compact?: boolean
  pending?: boolean
  projectLabel?: string
  onNavigate?: () => void
  onTogglePin: () => void
  onToggleArchived: () => void
  onMoveUp?: () => void
  onMoveDown?: () => void
}) {
  const client = useQueryClient()
  const projects = useChatProjects()
  const assign = useAssignChatProject()
  const remove = useDeleteAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const markViewed = () => {
    if (item.reviewPage) markReviewViewed(client, item.reviewPage, item.id)
    else markAgentThreadViewed(client, item.id)
  }
  useEffect(() => {
    if (!isActive || item.viewed) return
    if (item.reviewPage) markReviewViewed(client, item.reviewPage, item.id)
    else markAgentThreadViewed(client, item.id)
  }, [client, isActive, item.viewed, item.reviewPage, item.id])
  const deleting = remove.isPending && remove.variables === item.id
  return (
    <>
      <SidebarConversationRow
        title={item.title}
        threadId={item.id}
        status={item.status}
        viewed={item.viewed}
        active={isActive}
        compact={compact}
        pinned={pinned}
        archived={archived}
        pending={pending || deleting || assign.isPending}
        onNavigate={() => {
          if (item.reviewPage) noteReviewOpenedFromSidebar(item.reviewPage)
          markViewed()
          onNavigate?.()
        }}
        onPin={onTogglePin}
        onArchive={onToggleArchived}
        onDelete={() => setDeleteOpen(true)}
        projects={projects.data?.projects}
        projectId={projects.data?.memberships[item.id]}
        projectLabel={projectLabel}
        onMove={(projectId) => assign.mutate({ threadId: item.id, projectId })}
        onMoveUp={onMoveUp}
        onMoveDown={onMoveDown}
        reviewPage={item.reviewPage}
      />
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={item.title}
        isDeleting={deleting}
        onConfirm={() => {
          if (!deleting)
            remove.mutate(item.id, { onSuccess: () => setDeleteOpen(false) })
        }}
        detail="This deletes the conversation only. Local folders, worktrees, branches, and files are kept."
      />
    </>
  )
}
