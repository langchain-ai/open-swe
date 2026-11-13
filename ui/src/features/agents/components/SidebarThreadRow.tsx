import {
  ThreadDragCountContext,
  useThreadDragPreviewContent,
  useThreadDragging,
} from "@/features/agents/components/ThreadDragDrop.utils"
import {
  CaretDownIcon,
  CaretRightIcon,
  LaptopRegularIcon,
  XIcon,
} from "@langchain/macaw-components/icons"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@langchain/macaw-components/ContextMenu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { useZIndex } from "@langchain/macaw-components/utils/useZIndex"
import zIndices from "@langchain/macaw-components/utils/zIndices"
import { CheckCircleIcon } from "@phosphor-icons/react/dist/ssr/CheckCircle"
import { CloudIcon } from "@phosphor-icons/react/dist/ssr/Cloud"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { LockIcon } from "@phosphor-icons/react/dist/ssr/Lock"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { Link, useSearch } from "@tanstack/react-router"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"

import type { PullRequestSnapshot } from "@/features/agents/lib/api"
import type {
  AgentSubagentSummary,
  AgentThread,
} from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { parsePullRequestReference } from "@/features/reviews/search"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { useSidebarThreadSelection } from "@/features/agents/components/SidebarThreadSelection"
import { DropdownMenuItem } from "@/components/MenuItem"
import { runsOnAMac, useLocalThread } from "@/features/agents/lib/desktopLocal"
import {
  markReviewViewed,
  useDeleteAgentThread,
  useRenameAgentThread,
  useSetAgentThreadViewed,
} from "@/features/agents/lib/queries"
import {
  noteReviewOpenedFromSidebar,
  reviewPageRoute,
} from "@/features/reviews/lib/reviewEntry"
import { useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"

const ICON_SIZE = 14

const HOVER_TRIANGLE_EDGE_OFFSET = 1
const HOVER_TRIANGLE_BASE_PADDING = 15

type PrState = NonNullable<AgentThread["pr"]>["state"]

const PR_STATE_META: Record<
  PrState,
  { icon: IconComponent; label: string; className: string }
> = {
  draft: {
    icon: GitPullRequestIcon,
    label: "Draft pull request",
    className: "text-tertiary",
  },
  open: {
    icon: GitPullRequestIcon,
    label: "Open pull request",
    className: "text-success-secondary",
  },
  merged: {
    icon: GitMergeIcon,
    label: "Merged pull request",
    className: "text-purple",
  },
  closed: {
    icon: GitPullRequestIcon,
    label: "Closed pull request",
    className: "text-error-secondary",
  },
}

/** Codex-style compact age ("17m", "3h", "2d") — the tooltip has no room for prose. */
function compactAge(timestamp: number): string {
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000))
  if (minutes < 1) return "now"
  if (minutes < 60) return `${minutes}m`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  return days < 7 ? `${days}d` : `${Math.round(days / 7)}w`
}

function openContextMenuFromKeyboard(
  event: React.KeyboardEvent<HTMLAnchorElement>
) {
  if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10"))
    return
  event.preventDefault()
  const rect = event.currentTarget.getBoundingClientRect()
  event.currentTarget.dispatchEvent(
    new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
    })
  )
}

function SidebarRowTitle({ title }: { title: string }) {
  const moreCount = useContext(ThreadDragCountContext)
  return moreCount > 0 ? (
    <span className="flex min-w-0 flex-1 items-center gap-space-1 text-sm">
      <span className="truncate">{title}</span>
      <span className="shrink-0">(+ {moreCount} more)</span>
    </span>
  ) : (
    <span className="min-w-0 flex-1 truncate text-sm">{title}</span>
  )
}

function SidebarRowTitleInput({
  value,
  onChange,
  onSave,
  onCancel,
}: {
  value: string
  onChange: (value: string) => void
  onSave: () => void
  onCancel: () => void
}) {
  return (
    // A native field lets inline editing retain the navigation row's styling.
    <input
      data-thread-rename="input"
      aria-label="Thread title"
      placeholder="Thread title"
      className="min-w-0 flex-1 appearance-none border-0 bg-transparent p-0 text-sm text-inherit shadow-none ring-0 outline-none placeholder:text-placeholder focus:ring-0 focus:outline-none"
      value={value}
      maxLength={80}
      autoFocus
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => onChange(event.currentTarget.value)}
      onBlur={(event) => {
        // Prevent saving if the blur was caused by focusing to cancel button.
        if (
          event.relatedTarget instanceof HTMLElement &&
          event.relatedTarget.dataset.threadRename === "cancel"
        ) {
          return
        }

        onSave()
      }}
      onKeyDown={(event) => {
        event.stopPropagation()
        if (event.nativeEvent.isComposing) return
        if (event.key === "Enter") {
          event.preventDefault()
          onSave()
        } else if (event.key === "Escape") {
          event.preventDefault()
          onCancel()
        }
      }}
    />
  )
}

const TREE_STEP = 20

/** Props for a row `depth` levels below a top-level thread. */
function sidebarRowProps({
  active,
  archived,
  indent,
  depth,
}: {
  active: boolean
  archived: boolean
  indent: boolean
  depth: number
}): { className: string; style: React.CSSProperties } {
  return {
    className: cn(
      "relative flex h-8 items-center gap-space-2 rounded-lg pr-space-2 transition-colors",
      // Only ever on screen while "Show archived" is on; without this an
      // archived row is indistinguishable from a live one.
      archived && "opacity-55",
      "text-primary",
      active
        ? "bg-selected group-hover/row:bg-selected-hover group-has-data-[hover-preview=open]/row:bg-selected-hover"
        : "group-hover/row:bg-surface-level-2-hover group-has-data-[hover-preview=open]/row:bg-surface-level-2-hover"
    ),
    style: { paddingLeft: (indent ? 16 : 8) + depth * TREE_STEP },
  }
}

function RunningIndicator({ label }: { label: string }) {
  return (
    <span role="img" aria-label={label} className="flex shrink-0">
      <Spinner size="xs" className="size-3.5 text-icon-secondary" />
    </span>
  )
}

function ErrorIndicator({ label }: { label: string }) {
  return (
    <WarningCircleIcon
      size={ICON_SIZE}
      weight="regular"
      className="shrink-0 text-icon-error"
      aria-label={label}
    />
  )
}

function PullRequestIcon({
  state,
  live,
}: {
  state: PrState
  live?: PullRequestSnapshot
}) {
  const meta = PR_STATE_META[live?.state ?? state]
  const Glyph = meta.icon
  return (
    <Glyph
      size={ICON_SIZE}
      weight="regular"
      className={cn("shrink-0", meta.className)}
      aria-label={meta.label}
    />
  )
}

function PullRequestMenuItem({
  pr,
  live,
}: {
  pr: NonNullable<AgentThread["pr"]>
  live?: PullRequestSnapshot
}) {
  // Thread metadata records the state the PR had when it was opened; live
  // truth wins so a merged PR stops rendering as open.
  const meta = PR_STATE_META[live?.state ?? pr.state]
  const Glyph = meta.icon
  return (
    <DropdownMenuItem asChild>
      <a href={pr.url} target="_blank" rel="noreferrer">
        <Glyph size={ICON_SIZE} weight="regular" className={meta.className} />
        {meta.label} #{pr.number}
      </a>
    </DropdownMenuItem>
  )
}

function DisclosureCaret({
  expanded,
  visible,
  label,
  onToggle,
}: {
  expanded: boolean
  visible: boolean
  label: string
  onToggle: () => void
}) {
  const Caret = expanded ? CaretDownIcon : CaretRightIcon
  const toggle = (event: React.SyntheticEvent) => {
    event.preventDefault()
    event.stopPropagation()
    if (!visible) return
    onToggle()
  }
  return (
    <span
      role="button"
      data-thread-rename="caret"
      tabIndex={visible ? 0 : -1}
      aria-hidden={!visible}
      aria-expanded={expanded}
      aria-label={label}
      title={label}
      onClick={toggle}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") toggle(event)
      }}
      className={cn(
        "-mx-space-2 flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden",
        "text-tertiary hover:text-primary",
        "transition-all duration-normal motion-reduce:transition-none",
        !visible
          ? "pointer-events-none mr-0 w-0 opacity-0"
          : !expanded &&
              cn(
                "mr-0 w-0 opacity-0",
                "group-hover/row:-mr-space-2 group-hover/row:w-7 group-hover/row:opacity-100",
                "[@media(hover:none)]:-mr-space-2 [@media(hover:none)]:w-7 [@media(hover:none)]:opacity-100"
              )
      )}
    >
      <Caret size={16} className="shrink-0 translate-x-px" />
    </span>
  )
}

/**
 * Children of a sidebar row, joined to their parent by a guide line that
 * runs down from under the parent's title and curves into each child.
 */
function SidebarTree({
  label,
  indent,
  depth,
  children,
}: {
  label: string
  indent: boolean
  depth: number
  children: React.ReactNode
}) {
  const guide = (indent ? 16 : 8) + (depth - 1) * TREE_STEP + 6
  return (
    <ul
      aria-label={label}
      style={{ "--tree-x": `${guide}px` } as React.CSSProperties}
    >
      {children}
    </ul>
  )
}

/** The guide line for one `SidebarTree` child; render it last in the `li`. */
function TreeBranch() {
  return (
    <>
      <span
        aria-hidden
        className="pointer-events-none absolute -top-0.5 left-(--tree-x) h-4.5 w-2.5 rounded-bl-md border-b border-l border-subtle"
      />
      <span
        aria-hidden
        className="pointer-events-none absolute -top-0.5 -bottom-0.5 left-(--tree-x) border-l border-subtle [:last-child>&]:hidden"
      />
    </>
  )
}

export function SidebarThreadRow({
  item,
  isActive,
  activeThreadId,
  pinned,
  archived,
  live,
  indent = false,
  onNavigate,
  onTogglePin,
  onToggleArchived,
}: {
  item: SidebarThreadItem
  isActive: boolean
  activeThreadId?: string
  pinned: boolean
  archived: boolean
  live?: PullRequestSnapshot
  /** Nested under a repository: indent the content, not the highlight box. */
  indent?: boolean
  onNavigate?: () => void
  onTogglePin: () => void
  onToggleArchived: () => void
}) {
  const selection = useSidebarThreadSelection()
  const actionsZIndex = useZIndex(zIndices.popover) + 1
  const selected = selection?.selected.has(item.id) ?? false
  const queryClient = useQueryClient()
  const deleteThread = useDeleteAgentThread()
  const setViewed = useSetAgentThreadViewed()
  const worktreeThread = useLocalThread(item.id)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [titleDraft, setTitleDraft] = useState<string | null>(null)
  const renameThread = useRenameAgentThread()
  const editingTitle = useRef(false)

  useEffect(() => {
    if (
      !editingTitle.current &&
      titleDraft !== null &&
      (item.title === titleDraft.trim() || renameThread.isError)
    ) {
      setTitleDraft(null)
    }
  }, [item.title, titleDraft, renameThread.isError])
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)
  const menuNavigated = useRef(false)
  const activeSubagentId =
    useSearch({
      from: "/agents/$threadId",
      shouldThrow: false,
      select: (search) => search.subagent,
    }) ?? null

  const thread = item.thread
  const subagents = (item.subagents ?? []).filter(
    (subagent) => subagent.status !== "completed"
  )
  const workers = item.taskWorkers ?? []
  const activeWorkerId = workers.find(
    (worker) => worker.id === activeThreadId
  )?.id
  const openedThreadId = isActive ? item.id : activeWorkerId
  const [children, setChildren] = useState({
    openedThreadId,
    collapsed: false,
  })
  if (children.openedThreadId !== openedThreadId) {
    setChildren({ openedThreadId, collapsed: false })
  }
  const subagentsCollapsed = !openedThreadId || children.collapsed

  const hasSubagents = subagents.length > 0
  const activeSubagent =
    isActive &&
    activeSubagentId &&
    subagents.some((subagent) => subagent.toolCallId === activeSubagentId)
      ? activeSubagentId
      : null
  const rowIsActive =
    (isActive && (!activeSubagent || subagentsCollapsed)) ||
    (Boolean(activeWorkerId) && subagentsCollapsed)
  const unread = !item.viewed
  const isDeleting =
    deleteThread.isPending && deleteThread.variables === item.id

  const markViewed = () => {
    if (item.reviewPage) markReviewViewed(queryClient, item.reviewPage, item.id)
  }

  const onConfirmDelete = () => {
    if (isDeleting) return
    deleteThread.mutate(item.id, { onSuccess: () => setDeleteOpen(false) })
  }

  const handleNavigate = (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (contextMenuOpen || moreMenuOpen) {
      event.preventDefault()
      return
    }
    if (selection?.click(event, item)) return
    markViewed()
    onNavigate?.()
  }

  const saveTitle = () => {
    if (!editingTitle.current || titleDraft === null) return
    editingTitle.current = false
    const title = titleDraft.trim()
    if (title && title !== item.title) {
      renameThread.mutate({ threadId: item.id, title })
    } else {
      setTitleDraft(null)
    }
  }

  const cancelTitle = () => {
    editingTitle.current = false
    setTitleDraft(null)
  }

  const rowContent = (
    <>
      {selected && (
        <span className="text-brand text-xs" aria-label="Selected thread">
          ✓
        </span>
      )}
      {(hasSubagents || workers.length > 0) && (
        <DisclosureCaret
          visible={Boolean(openedThreadId)}
          expanded={!subagentsCollapsed}
          label={
            subagentsCollapsed ? "Show child threads" : "Hide child threads"
          }
          onToggle={() =>
            setChildren((previous) => ({
              ...previous,
              collapsed: !previous.collapsed,
            }))
          }
        />
      )}
      {titleDraft !== null ? (
        <SidebarRowTitleInput
          value={titleDraft}
          onChange={setTitleDraft}
          onSave={saveTitle}
          onCancel={cancelTitle}
        />
      ) : (
        <SidebarRowTitle title={item.title} />
      )}

      <span
        className={cn(
          "flex shrink-0 items-center justify-end gap-space-2 group-hover/row:invisible group-hover/row:min-w-6 group-has-data-[hover-preview=open]/row:invisible group-has-data-[hover-preview=open]/row:min-w-6 [@media(hover:none)]:pr-space-5",
          (moreMenuOpen || titleDraft !== null) && "invisible min-w-6"
        )}
      >
        {item.status === "running" || item.waitingForDelegatedWork ? (
          <RunningIndicator label="Thread running" />
        ) : item.status === "error" ? (
          <ErrorIndicator label="Thread error" />
        ) : unread ? (
          <span
            className="size-2 rounded-full bg-brand"
            aria-label="Unread thread"
          />
        ) : null}
      </span>
    </>
  )

  const rowProps = sidebarRowProps({
    active: selected || rowIsActive,
    archived,
    indent,
    depth: 0,
  })

  useThreadDragPreviewContent(
    item.id,
    <div
      {...rowProps}
      className={cn(
        rowProps.className,
        "group-data-[selected=true]/drag-preview:bg-selected-hover",
        selected || rowIsActive
          ? "bg-selected-hover"
          : "bg-surface-level-2-hover"
      )}
    >
      {rowContent}
    </div>
  )

  const review = item.reviewPage
  const link =
    titleDraft !== null ? (
      <div {...rowProps}>{rowContent}</div>
    ) : review ? (
      <Link
        {...reviewPageRoute(review)}
        onClick={(event) => {
          handleNavigate(event)
          if (!event.defaultPrevented) noteReviewOpenedFromSidebar(review)
        }}
        onKeyDown={openContextMenuFromKeyboard}
        {...rowProps}
        draggable
        data-selectable-thread={item.id}
        data-thread-title={item.title}
        data-thread-pinned={pinned}
        data-thread-archived={archived}
        aria-label={selected ? `${item.title}, selected` : item.title}
      >
        {rowContent}
      </Link>
    ) : (
      <Link
        to="/agents/$threadId"
        params={{ threadId: item.id }}
        onClick={handleNavigate}
        onKeyDown={openContextMenuFromKeyboard}
        {...rowProps}
        draggable
        data-selectable-thread={item.id}
        data-thread-title={item.title}
        data-thread-pinned={pinned}
        data-thread-archived={archived}
        aria-label={selected ? `${item.title}, selected` : item.title}
      >
        {rowContent}
      </Link>
    )

  const menuItems = (menu: "context" | "dropdown") => (
    <ThreadMenuItems
      menu={menu}
      thread={thread}
      pinned={pinned}
      archived={archived}
      isDeleting={isDeleting}
      onRename={() => {
        editingTitle.current = true
        requestAnimationFrame(() => setTitleDraft(item.title))
      }}
      onTogglePin={onTogglePin}
      onToggleViewed={() =>
        setViewed.mutate({ threadId: item.id, viewed: !item.viewed })
      }
      onToggleArchived={onToggleArchived}
      onDelete={() => setDeleteOpen(true)}
    />
  )

  return (
    <>
      <ContextMenu onOpenChange={setContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            data-sidebar-thread-row
            className={cn(
              "group/row relative mb-0.5 select-none [-webkit-touch-callout:none]",
              isDeleting && "opacity-50"
            )}
            onDoubleClick={(event) => {
              if (
                !(event.target instanceof Element) ||
                event.target.closest("[data-thread-rename]") ||
                renameThread.isPending
              ) {
                return
              }

              event.preventDefault()
              editingTitle.current = true
              setTitleDraft(item.title)
            }}
          >
            <RowHoverCard
              trigger={link}
              card={<ThreadHoverCard item={item} live={live} />}
            >
              {titleDraft !== null ? (
                <IconButton
                  data-thread-rename="cancel"
                  icon={XIcon}
                  label="Cancel rename"
                  size="xs"
                  color="secondary"
                  variant="plain"
                  className="absolute top-1/2 right-1 -translate-y-1/2 text-tertiary hover:text-primary"
                  onPointerDown={(event) => event.preventDefault()}
                  onClick={cancelTitle}
                  onBlur={cancelTitle}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault()
                      cancelTitle()
                    }
                  }}
                />
              ) : (
                <DropdownMenu onOpenChange={setMoreMenuOpen}>
                  <DropdownMenuTrigger asChild>
                    <IconButton
                      data-thread-rename="actions"
                      icon={DotsThreeIcon}
                      label="Thread actions"
                      style={
                        {
                          "--thread-actions-z-index": actionsZIndex,
                        } as React.CSSProperties
                      }
                      size="xs"
                      color="secondary"
                      variant="plain"
                      className={cn(
                        "absolute top-1/2 right-1 -translate-y-1/2 text-tertiary opacity-0 transition-opacity group-hover/row:opacity-100 group-has-data-[hover-preview=open]/row:z-(--thread-actions-z-index) group-has-data-[hover-preview=open]/row:opacity-100 hover:text-primary focus-visible:opacity-100 [@media(hover:none)]:opacity-100",
                        moreMenuOpen && "opacity-100"
                      )}
                    />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="end"
                    className="min-w-40 border-default bg-elevated"
                    onClickCapture={(event) => {
                      menuNavigated.current = Boolean(
                        (event.target as HTMLElement).closest("a[href]")
                      )
                    }}
                    onCloseAutoFocus={(event) => {
                      if (menuNavigated.current || editingTitle.current) {
                        event.preventDefault()
                      }
                      menuNavigated.current = false
                    }}
                  >
                    {item.pr && (
                      <>
                        <PullRequestMenuItem pr={item.pr} live={live} />
                        <DropdownMenuSeparator />
                      </>
                    )}
                    {menuItems("dropdown")}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </RowHoverCard>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent
          className="min-w-40 border-default bg-elevated"
          onCloseAutoFocus={(event) => {
            if (editingTitle.current) {
              event.preventDefault()
            }
          }}
        >
          {menuItems("context")}
        </ContextMenuContent>
      </ContextMenu>
      {openedThreadId && hasSubagents && !subagentsCollapsed && (
        <SidebarTree
          label={`Subagents of ${item.title}`}
          indent={indent}
          depth={1}
        >
          {subagents.map((subagent) => (
            <SidebarSubagentRow
              key={subagent.toolCallId}
              threadId={item.id}
              subagent={subagent}
              isActive={activeSubagent === subagent.toolCallId}
              indent={indent}
              depth={1}
              onNavigate={onNavigate}
            />
          ))}
        </SidebarTree>
      )}
      {openedThreadId && workers.length > 0 && !subagentsCollapsed && (
        <SidebarTree
          label={`Task workers of ${item.title}`}
          indent={indent}
          depth={1}
        >
          {workers.map((worker) => (
            <SidebarTaskWorkerRow
              key={worker.id}
              worker={worker}
              activeSubagentId={activeSubagentId}
              isActive={worker.id === activeThreadId}
              indent={indent}
              onNavigate={onNavigate}
            />
          ))}
        </SidebarTree>
      )}
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        isDeleting={isDeleting}
        onConfirm={onConfirmDelete}
        detail={
          worktreeThread &&
          !worktreeThread.managedTaskFolder &&
          worktreeThread.ownedWorktrees.length
            ? "This also deletes its worktree and uncommitted changes. Branch and commits are kept."
            : undefined
        }
      />
    </>
  )
}

/**
 * A subagent listed under the thread that spawned it. Opening it shows the
 * subagent's own transcript on the thread page; the parent row's `isActive`
 * moves here while it does.
 */
function SidebarSubagentRow({
  threadId,
  subagent,
  isActive,
  indent,
  depth,
  onNavigate,
}: {
  threadId: string
  subagent: AgentSubagentSummary
  isActive: boolean
  indent: boolean
  depth: number
  onNavigate?: () => void
}) {
  const link = (
    <Link
      to="/agents/$threadId"
      params={{ threadId }}
      search={{ subagent: subagent.toolCallId }}
      onClick={() => onNavigate?.()}
      {...sidebarRowProps({
        active: isActive,
        archived: false,
        indent,
        depth,
      })}
    >
      <SidebarRowTitle title={subagent.title} />
      <span className="flex shrink-0 items-center gap-space-2">
        {subagent.status === "error" && (
          <ErrorIndicator label="Subagent failed" />
        )}
        {subagent.status === "in_progress" && (
          <RunningIndicator label="Subagent running" />
        )}
      </span>
    </Link>
  )

  return (
    <li className="group/row relative mb-0.5">
      <RowHoverCard
        trigger={link}
        card={<SubagentHoverCard subagent={subagent} />}
      />
      <TreeBranch />
    </li>
  )
}

const RowHoverCardContext = createContext<{
  activeCardId: string | null
  dragging: boolean
  onOpenChange: (id: string, open: boolean) => void
}>({
  activeCardId: null,
  dragging: false,
  onOpenChange: (_id: string, _open: boolean) => {},
})

export function SidebarThreadHoverProvider(props: {
  children: React.ReactNode
}) {
  const [value, onChange] = useState<string | null>(null)
  const dragging = useThreadDragging(() => onChange(null))
  const onOpenChange = useCallback(
    (id: string, open: boolean) => {
      onChange((current) =>
        open && !dragging ? id : current === id ? null : current
      )
    },
    [dragging]
  )
  const hoverContext = useMemo(
    () => ({
      activeCardId: dragging ? null : value,
      dragging,
      onOpenChange,
    }),
    [value, dragging, onOpenChange]
  )

  return (
    <RowHoverCardContext value={hoverContext}>
      {props.children}
    </RowHoverCardContext>
  )
}

/** Rich preview beside a row; only one sidebar preview is open at a time. */
function RowHoverCard({
  trigger,
  card,
  children,
}: {
  trigger: React.ReactElement
  card: React.ReactNode
  children?: React.ReactNode
}) {
  const id = useId()
  const { activeCardId, dragging, onOpenChange } =
    useContext(RowHoverCardContext)
  const cardRef = useRef<HTMLDivElement>(null)
  const safeAreaRef = useRef<SVGSVGElement>(null)
  const triangleRef = useRef<SVGPolygonElement>(null)

  useEffect(() => () => onOpenChange(id, false), [id, onOpenChange])

  useEffect(() => {
    if (activeCardId !== id) return
    const abort = new AbortController()
    const dismiss = () => onOpenChange(id, false)
    document.addEventListener("scroll", dismiss, {
      capture: true,
      signal: abort.signal,
    })
    window.addEventListener("resize", dismiss, { signal: abort.signal })
    window.addEventListener("blur", dismiss, { signal: abort.signal })
    return () => abort.abort()
  }, [activeCardId, id, onOpenChange])

  const onPointerLeave = (event: React.PointerEvent) => {
    if (activeCardId !== id || event.pointerType === "touch") return

    const cardNode = cardRef.current?.getBoundingClientRect()
    const svgNode = safeAreaRef.current?.getBoundingClientRect()
    if (!cardNode || !svgNode) return

    const row = event.currentTarget.getBoundingClientRect()

    let points: string
    if (cardNode.left >= row.right || cardNode.right <= row.left) {
      const offset =
        cardNode.left >= row.right
          ? HOVER_TRIANGLE_EDGE_OFFSET
          : -HOVER_TRIANGLE_EDGE_OFFSET
      const edge = (offset > 0 ? cardNode.left : cardNode.right) + offset
      points = `${event.clientX - offset - svgNode.left},${event.clientY - svgNode.top} ${edge - svgNode.left},${cardNode.top - HOVER_TRIANGLE_BASE_PADDING - svgNode.top} ${edge - svgNode.left},${cardNode.bottom + HOVER_TRIANGLE_BASE_PADDING - svgNode.top}`
    } else {
      const offset =
        cardNode.top >= row.bottom
          ? HOVER_TRIANGLE_EDGE_OFFSET
          : -HOVER_TRIANGLE_EDGE_OFFSET
      const edge = (offset > 0 ? cardNode.top : cardNode.bottom) + offset
      points = `${event.clientX - svgNode.left},${event.clientY - offset - svgNode.top} ${cardNode.left - HOVER_TRIANGLE_BASE_PADDING - svgNode.left},${edge - svgNode.top} ${cardNode.right + HOVER_TRIANGLE_BASE_PADDING - svgNode.left},${edge - svgNode.top}`
    }
    triangleRef.current?.setAttribute("points", points)
  }

  return (
    <HoverCard
      open={!dragging && activeCardId === id}
      openDelay={activeCardId === null ? 500 : 0}
      closeDelay={100}
      onOpenChange={(open) => onOpenChange(id, open)}
    >
      <HoverCardTrigger
        asChild
        data-hover-preview={activeCardId === id ? "open" : "closed"}
        onPointerLeave={onPointerLeave}
      >
        <div>
          {trigger}
          {children}
        </div>
      </HoverCardTrigger>
      <HoverCardContent
        ref={cardRef}
        side="right"
        align="start"
        sideOffset={8}
        className="w-auto max-w-80 rounded-lg p-space-3"
      >
        {card}
        <svg
          ref={safeAreaRef}
          aria-hidden="true"
          data-hover-safe-area
          className="text-brand pointer-events-none absolute inset-0 h-full w-full overflow-visible"
        >
          <polygon
            ref={triangleRef}
            className="pointer-events-auto fill-transparent"
          />
        </svg>
      </HoverCardContent>
    </HoverCard>
  )
}

function ThreadHoverCard({
  item,
  live,
}: {
  item: SidebarThreadItem
  live?: PullRequestSnapshot
}) {
  const onAMac = runsOnAMac(item.thread)
  const LocationIcon = onAMac ? LaptopRegularIcon : CloudIcon
  const locationLabel = onAMac ? "This Mac" : "Cloud"
  const reference = parsePullRequestReference(item.pr?.url ?? "")
  const prContent = item.pr && (
    <>
      <PullRequestIcon state={item.pr.state} live={live} />
      <span className="min-w-0 truncate text-xxs">{item.pr.title}</span>
    </>
  )
  const prClassName =
    "pointer-events-auto -mx-space-1 flex min-w-0 items-center gap-space-2 rounded-md px-space-1 py-0.5 text-secondary hover:bg-surface-level-1-hover hover:text-primary"

  return (
    <div className="flex min-w-0 flex-col gap-space-2">
      <div className="flex items-start gap-space-2">
        <span className="min-w-0 flex-1 text-xs font-medium text-primary">
          {item.title}
        </span>
        {item.thread.visibility === "private" && (
          <LockIcon
            size={ICON_SIZE}
            weight="regular"
            className="mt-0.5 shrink-0 text-icon-secondary"
            aria-label="Private thread"
          />
        )}
        <LocationIcon
          size={ICON_SIZE}
          weight="regular"
          className="mt-0.5 shrink-0 text-icon-secondary"
          aria-label={locationLabel}
        />
        <span className="mt-px shrink-0 text-xxs text-secondary">
          {compactAge(item.updatedAt)}
        </span>
      </div>
      {item.repoLabel && (
        <div className="flex min-w-0 items-center gap-space-2 text-secondary">
          <FolderIcon size={ICON_SIZE} weight="regular" className="shrink-0" />
          <span className="min-w-0 truncate text-xxs">{item.repoLabel}</span>
        </div>
      )}
      {item.pr &&
        (reference ? (
          <Link
            {...reviewPageRoute(reference)}
            onClick={(event) => {
              noteReviewOpenedFromSidebar(reference)
              event.stopPropagation()
            }}
            className={prClassName}
          >
            {prContent}
          </Link>
        ) : (
          <a
            href={item.pr.url}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
            className={prClassName}
          >
            {prContent}
          </a>
        ))}
    </div>
  )
}

function SubagentHoverCard({ subagent }: { subagent: AgentSubagentSummary }) {
  return (
    <div className="flex min-w-0 flex-col gap-space-2">
      <div className="flex items-start gap-space-2">
        <span className="min-w-0 flex-1 text-xs font-medium text-primary">
          {subagent.title}
        </span>
        {subagent.status === "in_progress" ? (
          <span className="mt-0.5 flex shrink-0">
            <RunningIndicator label="Subagent running" />
          </span>
        ) : subagent.status === "error" ? (
          <span className="mt-0.5 flex shrink-0">
            <ErrorIndicator label="Subagent failed" />
          </span>
        ) : (
          <CheckCircleIcon
            size={ICON_SIZE}
            weight="regular"
            className="mt-0.5 shrink-0 text-icon-secondary"
            aria-label="Subagent finished"
          />
        )}
        <span className="mt-px shrink-0 text-xxs text-secondary">
          {compactAge(subagent.endedAt ?? subagent.startedAt)}
        </span>
      </div>
      <div className="flex min-w-0 items-center gap-space-2 text-secondary">
        <RobotIcon size={ICON_SIZE} weight="regular" className="shrink-0" />
        <span className="min-w-0 truncate text-xxs">
          {subagent.subagentType}
        </span>
      </div>
    </div>
  )
}

function SidebarTaskWorkerRow({
  worker,
  activeSubagentId,
  isActive,
  indent,
  onNavigate,
}: {
  worker: AgentThread
  activeSubagentId: string | null
  isActive: boolean
  indent: boolean
  onNavigate?: () => void
}) {
  const [children, setChildren] = useState({ isActive, collapsed: false })
  if (children.isActive !== isActive) {
    setChildren({ isActive, collapsed: false })
  }

  const subagentsCollapsed = !isActive || children.collapsed
  const subagents = (worker.subagents ?? []).filter(
    (subagent) => subagent.status !== "completed"
  )
  const activeSubagent =
    isActive &&
    subagents.some((subagent) => subagent.toolCallId === activeSubagentId)
  const rowIsActive = isActive && (!activeSubagent || subagentsCollapsed)
  const status = worker.pendingWorkflowApproval
    ? "Awaiting approval"
    : worker.status === "finished"
      ? "Completed"
      : worker.status === "error"
        ? "Failed"
        : worker.status === "interrupted"
          ? "Interrupted"
          : worker.status === "running"
            ? "Running"
            : "Idle"
  return (
    <li className="group/row relative mb-0.5">
      <Link
        to="/agents/$threadId"
        params={{ threadId: worker.id }}
        search={{}}
        aria-current={rowIsActive ? "page" : undefined}
        title={`${worker.title} — ${status}`}
        onClick={onNavigate}
        {...sidebarRowProps({
          active: rowIsActive,
          archived: worker.resolved === true,
          indent,
          depth: 1,
        })}
      >
        {subagents.length > 0 && (
          <DisclosureCaret
            visible={isActive}
            expanded={!subagentsCollapsed}
            label={
              subagentsCollapsed
                ? "Show worker subagents"
                : "Hide worker subagents"
            }
            onToggle={() =>
              setChildren((previous) => ({
                ...previous,
                collapsed: !previous.collapsed,
              }))
            }
          />
        )}
        <SidebarRowTitle title={worker.title} />
        <span className="flex shrink-0 items-center gap-space-1 text-xxs text-secondary">
          {worker.status === "running" && (
            <RunningIndicator label="Worker running" />
          )}
          {worker.status === "error" && (
            <ErrorIndicator label="Worker failed" />
          )}
          {status}
        </span>
      </Link>
      {isActive && subagents.length > 0 && !subagentsCollapsed && (
        <SidebarTree
          label={`Subagents of ${worker.title}`}
          indent={indent}
          depth={2}
        >
          {subagents.map((subagent) => (
            <SidebarSubagentRow
              key={subagent.toolCallId}
              threadId={worker.id}
              subagent={subagent}
              isActive={isActive && activeSubagentId === subagent.toolCallId}
              indent={indent}
              depth={2}
              onNavigate={onNavigate}
            />
          ))}
        </SidebarTree>
      )}
      <TreeBranch />
    </li>
  )
}
