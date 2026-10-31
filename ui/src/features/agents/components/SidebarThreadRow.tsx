import {
  CaretDownIcon,
  CaretRightIcon,
  LaptopRegularIcon,
  PushPinRegularIcon,
} from "@langchain/macaw-components/icons"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from "@langchain/macaw-components/ContextMenu"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { CopyIconButton } from "@langchain/macaw-components/CopyButton"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { BookOpenTextIcon } from "@phosphor-icons/react/dist/ssr/BookOpenText"
import { CalendarBlankIcon } from "@phosphor-icons/react/dist/ssr/CalendarBlank"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { CheckCircleIcon } from "@phosphor-icons/react/dist/ssr/CheckCircle"
import { CloudIcon } from "@phosphor-icons/react/dist/ssr/Cloud"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { LockIcon } from "@phosphor-icons/react/dist/ssr/Lock"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { TerminalWindowIcon } from "@phosphor-icons/react/dist/ssr/TerminalWindow"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { Link, useSearch } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"
import type { SVGProps } from "react"

import type { PullRequestSnapshot } from "@/features/agents/lib/api"
import type {
  AgentSource,
  AgentSubagentSummary,
  AgentThread,
} from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { OpenPullRequestButton } from "@/features/agents/components/OpenPullRequestButton"
import {
  SELECTABLE_ROW_ATTRIBUTE,
  SidebarBulkMenuItems,
  useSidebarSelection,
} from "@/features/agents/components/SidebarSelection"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { threadPullRequest } from "@/features/agents/lib/threadPullRequest"
import { runsOnAMac, useLocalThread } from "@/features/agents/lib/desktopLocal"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  markAgentThreadViewed,
  markReviewViewed,
  useDeleteAgentThread,
  useThreadTitleRegeneration,
} from "@/features/agents/lib/queries"
import {
  noteReviewOpenedFromSidebar,
  reviewPageRoute,
} from "@/features/reviews/lib/reviewEntry"
import { useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"

/** Phosphor has no Linear mark; this is Linear's own logo. */
function LinearLogoIcon({
  size = 16,
  weight: _weight,
  mirrored: _mirrored,
  ...props
}: SVGProps<SVGSVGElement> & {
  size?: string | number
  weight?: unknown
  mirrored?: boolean
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      {...props}
    >
      <path d="M2.886 4.18A11.982 11.982 0 0 1 11.99 0C18.624 0 24 5.376 24 12.009c0 3.64-1.62 6.903-4.18 9.105L2.887 4.18ZM1.817 5.626l16.556 16.556c-.524.33-1.075.62-1.65.866L.951 7.277c.247-.575.537-1.126.866-1.65ZM.322 9.163l14.515 14.515c-.71.172-1.443.282-2.195.322L0 11.358a12 12 0 0 1 .322-2.195Zm-.17 4.862 9.823 9.824a12.02 12.02 0 0 1-9.824-9.824Z" />
    </svg>
  )
}

const ICON_SIZE = 14

const SOURCE_META: Record<AgentSource, { icon: IconComponent; label: string }> =
  {
    dashboard: { icon: ChatCircleIcon, label: "Started from the dashboard" },
    github: { icon: GithubLogoIcon, label: "Triggered from GitHub" },
    slack: { icon: SlackLogoIcon, label: "Triggered from Slack" },
    linear: { icon: LinearLogoIcon, label: "Triggered from Linear" },
    schedule: { icon: CalendarBlankIcon, label: "Triggered from a schedule" },
    claude_code: {
      icon: TerminalWindowIcon,
      label: "Mirrored from Claude Code",
    },
  }

const THIS_MAC_META = { icon: LaptopRegularIcon, label: "Runs on this Mac" }

function sourceMeta(item: SidebarThreadItem) {
  if (item.source && item.source !== "dashboard")
    return SOURCE_META[item.source]
  return runsOnAMac(item.thread) ? THIS_MAC_META : null
}

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

/** Shift-click extends the selection; without this it also selects the titles' text. */
function keepTextUnselectedOnShiftClick(event: React.MouseEvent) {
  if (event.shiftKey) event.preventDefault()
}

/** Pixels per second every title travels at, whatever its length. */
const MARQUEE_SPEED = 45
/** Keeps a barely-overflowing title from flicking past in a few frames. */
const MARQUEE_MIN_DURATION = 0.5

/**
 * Slides an overflowing title far enough to read its tail while hovered. The
 * shift is measured on enter rather than tracked continuously because it only
 * matters for the row the pointer is actually over, and the duration is derived
 * from it so long and short titles read at the same speed.
 */
function useTitleMarquee() {
  const viewport = useRef<HTMLSpanElement>(null)
  const text = useRef<HTMLSpanElement>(null)
  const [shift, setShift] = useState(0)

  const measure = () => {
    const overflow =
      (text.current?.scrollWidth ?? 0) - (viewport.current?.clientWidth ?? 0)
    setShift(overflow > 4 ? -overflow : 0)
  }

  const duration = Math.max(
    MARQUEE_MIN_DURATION,
    Math.abs(shift) / MARQUEE_SPEED
  )

  return { viewport, text, shift, duration, measure, reset: () => setShift(0) }
}

function SidebarRowTitle({
  marquee: { viewport, text, shift, duration },
  title,
}: {
  marquee: ReturnType<typeof useTitleMarquee>
  title: string
}) {
  return (
    <span
      ref={viewport}
      className={cn(
        "sidebar-title-viewport relative min-w-0 flex-1 overflow-hidden",
        shift !== 0 && "sidebar-title-marquee-mask"
      )}
    >
      <span
        ref={text}
        className={cn(
          "block w-max text-sm whitespace-nowrap will-change-transform",
          shift !== 0 && "sidebar-title-marquee"
        )}
        style={
          {
            "--marquee-shift": `${shift}px`,
            "--marquee-duration": `${duration}s`,
          } as React.CSSProperties
        }
      >
        {title}
      </span>
    </span>
  )
}

function sidebarRowClassName({
  compact,
  active,
  selected = false,
  paddingLeft,
  archived,
}: {
  compact: boolean
  active: boolean
  selected?: boolean
  paddingLeft: string
  archived: boolean
}): string {
  return cn(
    "flex items-center gap-space-2 rounded-lg pr-space-2 transition-colors",
    paddingLeft,
    // Only ever on screen while "Show archived" is on; without this an
    // archived row is indistinguishable from a live one.
    archived && "opacity-55",
    compact ? "h-7 gap-space-2" : "h-8",
    "text-primary",
    active || selected
      ? "bg-selected group-hover/row:bg-selected-hover"
      : "group-hover/row:bg-surface-level-2-hover",
    selected && "ring-brand ring-1 ring-inset"
  )
}

export function SidebarConversationRow({
  title,
  threadId,
  active,
  compact,
  onNavigate,
  pinned,
  archived,
  pending,
  onPin,
  onArchive,
}: {
  title: string
  threadId: string
  active: boolean
  compact: boolean
  onNavigate: () => void
  pinned: boolean
  archived: boolean
  pending: boolean
  onPin: () => void
  onArchive: () => void
}) {
  const marquee = useTitleMarquee()
  return (
    <div
      className="group/row relative mb-0.5"
      onMouseEnter={marquee.measure}
      onMouseLeave={marquee.reset}
    >
      <Link
        to="/agents/$threadId"
        params={{ threadId }}
        aria-current={active ? "page" : undefined}
        onClick={onNavigate}
        className={cn(
          "flex items-center rounded-xl px-space-2 text-secondary transition-colors group-focus-within/row:bg-surface-level-3 group-focus-within/row:pr-14 group-hover/row:bg-surface-level-3 group-hover/row:pr-14 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
          compact ? "h-7" : "h-8",
          active && "bg-surface-level-3"
        )}
      >
        <SidebarRowTitle title={title} marquee={marquee} />
      </Link>
      <span className="pointer-events-none absolute inset-y-0 right-1 flex items-center gap-0.5 rounded-xl bg-surface-level-3 opacity-0 group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100 group-hover/row:pointer-events-auto group-hover/row:opacity-100">
        <IconButton
          icon={pinned ? PushPinSlashIcon : PushPinRegularIcon}
          label={pinned ? "Unpin chat" : "Pin chat"}
          className="text-tertiary hover:text-secondary"
          size="xs"
          color="secondary"
          variant="plain"
          disabled={pending}
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onPin()
          }}
        />
        <IconButton
          icon={archived ? ArrowCounterClockwiseIcon : ArchiveIcon}
          label={archived ? "Unarchive chat" : "Archive chat"}
          className="text-tertiary hover:text-secondary"
          size="xs"
          color="secondary"
          variant="plain"
          disabled={pending}
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onArchive()
          }}
        />
      </span>
    </div>
  )
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
  className,
}: {
  state: PrState
  live?: PullRequestSnapshot
  className?: string
}) {
  // Thread metadata records the state the PR had when it was opened; live
  // truth wins so a merged PR stops rendering as open.
  const meta = PR_STATE_META[live?.state ?? state]
  const Glyph = meta.icon
  return (
    <span
      className={cn("relative flex shrink-0", className)}
      title={meta.label}
    >
      <Glyph
        size={ICON_SIZE}
        weight="regular"
        className={meta.className}
        aria-label={meta.label}
      />
      {live?.checks === "failing" && (
        <span
          className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-error-strong ring-2 ring-surface-level-2"
          aria-label="Checks failing"
        />
      )}
    </span>
  )
}

export function SidebarThreadRow({
  item,
  isActive,
  activeThreadId,
  pinned,
  archived,
  live,
  compact = false,
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
  compact?: boolean
  /** Nested under a repository: indent the content, not the highlight box. */
  indent?: boolean
  onNavigate?: () => void
  onTogglePin: () => void
  onToggleArchived: () => void
}) {
  const queryClient = useQueryClient()
  const deleteThread = useDeleteAgentThread()
  const { regenerating, regenerate: regenerateTitle } =
    useThreadTitleRegeneration(item.id)
  const selection = useSidebarSelection()
  const selected = Boolean(selection?.selected.has(item.key))
  const bulkMenu = selected && (selection?.selected.size ?? 0) > 1
  useEffect(() => selection?.register(item), [selection, item])
  const pullRequest = threadPullRequest(item.thread)
  const worktreeThread = useLocalThread(item.id)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const marquee = useTitleMarquee()
  const { prefs, toggleSubagentsCollapsed } = useSidebarPrefs()
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
  const [workersExpanded, setWorkersExpanded] = useState(
    Boolean(activeWorkerId)
  )
  useEffect(() => {
    if (activeWorkerId) setWorkersExpanded(true)
  }, [activeWorkerId])
  const hasSubagents = subagents.length > 0
  const activeSubagent =
    isActive &&
    activeSubagentId &&
    subagents.some((subagent) => subagent.toolCallId === activeSubagentId)
      ? activeSubagentId
      : null
  const subagentsCollapsed = !prefs.expandedSubagentKeys.includes(item.key)
  const rowIsActive =
    (isActive && (!activeSubagent || subagentsCollapsed)) ||
    (Boolean(activeWorkerId) && !workersExpanded)
  const source = sourceMeta(item)
  const SourceIcon = source?.icon
  // Strictly an unread marker, not a "finished" one: any thread the user has
  // not opened since its latest run shows the dot. The focused thread is being
  // read right now, so it never does — derived rather than left to the
  // optimistic cache patch, which a list refetch can overwrite.
  const unread = !item.viewed && !isActive
  const isDeleting =
    deleteThread.isPending && deleteThread.variables === item.id

  const markViewed = () => {
    if (item.reviewPage) markReviewViewed(queryClient, item.reviewPage, item.id)
    else markAgentThreadViewed(queryClient, item.id)
  }

  // Covers every way a row becomes active — click, command palette, keyboard
  // nav, browser back — not just the click handler below.
  useEffect(() => {
    if (isActive && !item.viewed) markViewed()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, item.viewed])

  const onConfirmDelete = () => {
    if (isDeleting) return
    deleteThread.mutate(item.id, { onSuccess: () => setDeleteOpen(false) })
  }

  const onArchiveClick = (event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    onToggleArchived()
  }

  const onPinClick = (event: React.MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    onTogglePin()
  }

  const handleNavigate = (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (contextMenuOpen || selection?.handleClick(event, item)) {
      event.preventDefault()
      return
    }
    markViewed()
    onNavigate?.()
  }

  const onToggleSubagents = (event: React.SyntheticEvent) => {
    event.preventDefault()
    event.stopPropagation()
    toggleSubagentsCollapsed(item.key)
  }
  const SubagentCaret = subagentsCollapsed ? CaretRightIcon : CaretDownIcon

  const rowContent = (
    <>
      {hasSubagents && (
        <span
          role="button"
          tabIndex={0}
          aria-expanded={!subagentsCollapsed}
          aria-label={subagentsCollapsed ? "Show subagents" : "Hide subagents"}
          title={subagentsCollapsed ? "Show subagents" : "Hide subagents"}
          onClick={onToggleSubagents}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ")
              onToggleSubagents(event)
          }}
          className="flex h-5 w-4 shrink-0 items-center justify-center text-tertiary transition-colors hover:text-primary"
        >
          <SubagentCaret size={12} weight="bold" />
        </span>
      )}
      {workers.length > 0 && (
        <span
          role="button"
          tabIndex={0}
          aria-expanded={workersExpanded}
          aria-label={
            workersExpanded ? "Hide task workers" : "Show task workers"
          }
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            setWorkersExpanded((expanded) => !expanded)
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault()
              event.stopPropagation()
              setWorkersExpanded((expanded) => !expanded)
            }
          }}
          className="flex h-5 w-4 shrink-0 items-center justify-center text-tertiary transition-colors hover:text-primary"
        >
          {workersExpanded ? (
            <CaretDownIcon size={12} weight="bold" />
          ) : (
            <CaretRightIcon size={12} weight="bold" />
          )}
        </span>
      )}
      <SidebarRowTitle marquee={marquee} title={item.title} />
      {selected && <span className="sr-only">Selected</span>}

      <span className="flex shrink-0 items-center gap-space-2 group-hover/row:hidden">
        {regenerating && <RunningIndicator label="Regenerating title" />}
        {item.status === "error" && <ErrorIndicator label="Thread error" />}
        {thread.automationActionPosted && (
          <SlackLogoIcon
            size={ICON_SIZE}
            weight="regular"
            className="text-icon-success"
            aria-label="Action posted to Slack"
          />
        )}
        {item.reviewPage ? (
          <BookOpenTextIcon
            size={ICON_SIZE}
            weight="regular"
            className="text-icon-tertiary"
            aria-label="Pull request review"
          />
        ) : (
          <>
            {source && SourceIcon && !item.pr && (
              <SourceIcon
                size={ICON_SIZE}
                weight="regular"
                className="text-icon-tertiary"
                aria-label={source.label}
              />
            )}
            {item.pr && <PullRequestIcon state={item.pr.state} live={live} />}
          </>
        )}
        {item.status === "running" ? (
          <RunningIndicator label="Thread running" />
        ) : unread ? (
          <span
            className="size-2 rounded-full bg-brand"
            aria-label="Unread thread"
          />
        ) : null}
      </span>

      <span className="-mr-[3px] hidden shrink-0 items-center gap-0.5 group-hover/row:flex">
        {pullRequest && !item.reviewPage && (
          <OpenPullRequestButton
            pullRequest={pullRequest}
            onNavigate={onNavigate}
          />
        )}
        <IconButton
          icon={pinned ? PushPinSlashIcon : PushPinRegularIcon}
          label={pinned ? "Unpin thread" : "Pin thread"}
          size="xs"
          color="secondary"
          variant="plain"
          onClick={onPinClick}
        />
        <IconButton
          icon={archived ? ArrowCounterClockwiseIcon : ArchiveIcon}
          label={archived ? "Unarchive thread" : "Archive thread"}
          size="xs"
          color="secondary"
          variant="plain"
          onClick={onArchiveClick}
        />
      </span>
    </>
  )

  const rowClassName = sidebarRowClassName({
    compact,
    active: rowIsActive,
    selected,
    paddingLeft:
      hasSubagents || workers.length > 0
        ? indent
          ? "pl-space-4"
          : "pl-space-2"
        : indent
          ? "pl-space-5"
          : "pl-space-2",
    archived,
  })

  const review = item.reviewPage
  const link = review ? (
    <Link
      {...reviewPageRoute(review)}
      onClick={(event) => {
        noteReviewOpenedFromSidebar(review)
        handleNavigate(event)
      }}
      onKeyDown={openContextMenuFromKeyboard}
      onMouseDown={keepTextUnselectedOnShiftClick}
      className={rowClassName}
    >
      {rowContent}
    </Link>
  ) : (
    <Link
      to="/agents/$threadId"
      params={{ threadId: item.id }}
      onClick={handleNavigate}
      onKeyDown={openContextMenuFromKeyboard}
      onMouseDown={keepTextUnselectedOnShiftClick}
      className={rowClassName}
    >
      {rowContent}
    </Link>
  )

  return (
    <>
      <ContextMenu onOpenChange={setContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            {...{ [SELECTABLE_ROW_ATTRIBUTE]: item.key }}
            className={cn(
              "group/row relative mb-0.5",
              isDeleting && "opacity-50"
            )}
            onMouseEnter={marquee.measure}
            onMouseLeave={marquee.reset}
          >
            <RowHoverCard
              trigger={link}
              card={<ThreadHoverCard item={item} live={live} />}
              suppressed={contextMenuOpen}
            />
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="min-w-[10rem]">
          {bulkMenu ? (
            <SidebarBulkMenuItems menu="context" />
          ) : (
            <ThreadMenuItems
              menu="context"
              thread={thread}
              pinned={pinned}
              archived={archived}
              isDeleting={isDeleting}
              regeneratingTitle={regenerating}
              onTogglePin={onTogglePin}
              onToggleArchived={onToggleArchived}
              onRegenerateTitle={regenerateTitle}
              onDelete={() => setDeleteOpen(true)}
            />
          )}
        </ContextMenuContent>
      </ContextMenu>
      {hasSubagents && !subagentsCollapsed && (
        <ul aria-label={`Subagents of ${item.title}`}>
          {subagents.map((subagent) => (
            <SidebarSubagentRow
              key={subagent.toolCallId}
              threadId={item.id}
              subagent={subagent}
              isActive={activeSubagent === subagent.toolCallId}
              compact={compact}
              indent={indent}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      )}
      {workers.length > 0 && workersExpanded && (
        <ul aria-label={`Task workers of ${item.title}`}>
          {workers.map((worker) => (
            <SidebarTaskWorkerRow
              key={worker.id}
              worker={worker}
              activeSubagentId={activeSubagentId}
              isActive={worker.id === activeThreadId}
              compact={compact}
              indent={indent}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      )}
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={item.title}
        isDeleting={isDeleting}
        onConfirm={onConfirmDelete}
        detail={
          !worktreeThread
            ? undefined
            : worktreeThread.managedTaskFolder
              ? `This deletes the conversation. Its task files are kept at ${worktreeThread.cwd}.`
              : worktreeThread.ownedWorktrees.length
                ? "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
                : "This removes its history but does not revert changes made to your repository."
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
  nested = false,
  compact,
  indent,
  onNavigate,
}: {
  threadId: string
  subagent: AgentSubagentSummary
  isActive: boolean
  nested?: boolean
  compact: boolean
  indent: boolean
  onNavigate?: () => void
}) {
  const marquee = useTitleMarquee()

  const link = (
    <Link
      to="/agents/$threadId"
      params={{ threadId }}
      search={{ subagent: subagent.toolCallId }}
      onClick={() => onNavigate?.()}
      className={sidebarRowClassName({
        compact,
        active: isActive,
        paddingLeft: nested
          ? indent
            ? "pl-18"
            : "pl-space-9"
          : indent
            ? "pl-13.5"
            : "pl-11.5",
        archived: false,
      })}
    >
      <SidebarRowTitle marquee={marquee} title={subagent.title} />
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
    <li
      className="group/row relative mb-0.5"
      onMouseEnter={marquee.measure}
      onMouseLeave={marquee.reset}
    >
      <RowHoverCard
        trigger={link}
        card={<SubagentHoverCard subagent={subagent} />}
      />
    </li>
  )
}

/** Rich preview beside a row; it is a hover card so its links stay clickable. */
function RowHoverCard({
  trigger,
  card,
  suppressed = false,
}: {
  trigger: React.ReactElement
  card: React.ReactNode
  /** Kept shut while the row's context menu is open, which it would cover. */
  suppressed?: boolean
}) {
  return (
    <HoverCard
      openDelay={500}
      closeDelay={100}
      open={suppressed ? false : undefined}
    >
      <HoverCardTrigger asChild>{trigger}</HoverCardTrigger>
      <HoverCardContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-auto max-w-80 rounded-lg p-space-3"
      >
        {card}
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
      {item.pr && (
        <div className="-mx-space-1 flex min-w-0 items-center gap-0.5">
          <a
            href={item.pr.url}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="pointer-events-auto flex min-w-0 flex-1 items-center gap-space-2 rounded-md px-space-1 py-0.5 text-secondary hover:bg-surface-level-1-hover hover:text-primary"
          >
            <PullRequestIcon state={item.pr.state} live={live} />
            <span className="min-w-0 truncate text-xxs">{item.pr.title}</span>
          </a>
          <CopyIconButton
            copy={item.pr.url}
            label="Copy PR link"
            size="xs"
            className="pointer-events-auto shrink-0"
          />
        </div>
      )}
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
  compact,
  indent,
  onNavigate,
}: {
  worker: AgentThread
  activeSubagentId: string | null
  isActive: boolean
  compact: boolean
  indent: boolean
  onNavigate?: () => void
}) {
  const queryClient = useQueryClient()
  const marquee = useTitleMarquee()
  const { prefs, toggleSubagentsCollapsed } = useSidebarPrefs()
  const subagents = (worker.subagents ?? []).filter(
    (subagent) => subagent.status !== "completed"
  )
  const activeSubagent =
    isActive &&
    subagents.some((subagent) => subagent.toolCallId === activeSubagentId)
  const subagentsCollapsed = !prefs.expandedSubagentKeys.includes(
    `cloud:${worker.id}`
  )
  const rowIsActive = isActive && (!activeSubagent || subagentsCollapsed)
  const SubagentCaret = subagentsCollapsed ? CaretRightIcon : CaretDownIcon
  const status =
    worker.status === "finished"
      ? "Completed"
      : worker.status === "error"
        ? "Failed"
        : worker.status === "interrupted"
          ? "Interrupted"
          : worker.status === "running"
            ? "Running"
            : "Idle"
  return (
    <li
      className="group/row relative mb-0.5"
      onMouseEnter={marquee.measure}
      onMouseLeave={marquee.reset}
    >
      <Link
        to="/agents/$threadId"
        params={{ threadId: worker.id }}
        search={{}}
        aria-current={rowIsActive ? "page" : undefined}
        title={`${worker.title} — ${status}`}
        onClick={() => {
          markAgentThreadViewed(queryClient, worker.id)
          onNavigate?.()
        }}
        className={sidebarRowClassName({
          compact,
          active: rowIsActive,
          paddingLeft: indent ? "pl-13.5" : "pl-11.5",
          archived: worker.resolved === true,
        })}
      >
        {subagents.length > 0 && (
          <span
            role="button"
            tabIndex={0}
            aria-expanded={!subagentsCollapsed}
            aria-label={
              subagentsCollapsed
                ? "Show worker subagents"
                : "Hide worker subagents"
            }
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              toggleSubagentsCollapsed(`cloud:${worker.id}`)
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                event.stopPropagation()
                toggleSubagentsCollapsed(`cloud:${worker.id}`)
              }
            }}
            className="flex h-5 w-4 shrink-0 items-center justify-center text-tertiary transition-colors hover:text-primary"
          >
            <SubagentCaret size={12} weight="bold" />
          </span>
        )}
        <RobotIcon
          size={ICON_SIZE}
          weight="regular"
          className="shrink-0 text-icon-secondary"
          aria-label="Asynchronous task worker"
        />
        <SidebarRowTitle marquee={marquee} title={worker.title} />
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
      {subagents.length > 0 && !subagentsCollapsed && (
        <ul aria-label={`Subagents of ${worker.title}`}>
          {subagents.map((subagent) => (
            <SidebarSubagentRow
              key={subagent.toolCallId}
              threadId={worker.id}
              subagent={subagent}
              isActive={isActive && activeSubagentId === subagent.toolCallId}
              compact={compact}
              indent={indent}
              nested
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      )}
    </li>
  )
}
