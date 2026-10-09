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
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle"
import { Link, useNavigate, useSearch } from "@tanstack/react-router"
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
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { runsOnAMac, useLocalThread } from "@/features/agents/lib/desktopLocal"
import { useMarkLegacyLocalThreadViewed } from "@/features/agents/lib/legacyLocal"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  markAgentThreadViewed,
  markReviewViewed,
  useDeleteAgentThread,
} from "@/features/agents/lib/queries"
import {
  noteReviewOpenedFromSidebar,
  reviewPageRoute,
} from "@/features/reviews/lib/reviewEntry"
import { useQueryClient } from "@tanstack/react-query"
import { cn } from "@/lib/utils"
import { useChatRoutes } from "@/lib/chatRoutes"
import { reportError } from "@/lib/errorReporting"

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
    dashboard: { icon: ChatCircleIcon, label: "Started from the web app" },
    github: { icon: GithubLogoIcon, label: "Triggered from GitHub" },
    slack: { icon: SlackLogoIcon, label: "Triggered from Slack" },
    linear: { icon: LinearLogoIcon, label: "Triggered from Linear" },
    schedule: { icon: CalendarBlankIcon, label: "Triggered from a schedule" },
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
  paddingLeft,
  archived,
}: {
  compact: boolean
  active: boolean
  paddingLeft: string
  archived: boolean
}): string {
  return cn(
    "flex items-center gap-2 rounded-lg pr-2.5 transition-colors",
    paddingLeft,
    // Only ever on screen while "Show archived" is on; without this an
    // archived row is indistinguishable from a live one.
    archived && "opacity-55",
    compact ? "h-7 gap-1.5" : "h-8",
    "text-primary",
    active
      ? "bg-selected group-hover/row:bg-selected-hover"
      : "group-hover/row:bg-surface-level-2-hover"
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
  onDeleteLocal,
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
  onDeleteLocal: (threadId?: string) => void
  onTogglePin: () => void
  onToggleArchived: () => void
}) {
  const navigate = useNavigate()
  const chat = useChatRoutes()
  const queryClient = useQueryClient()
  const markLocalViewed = useMarkLegacyLocalThreadViewed()
  const deleteThread = useDeleteAgentThread()
  const worktreeThread =
    useLocalThread(item.id) ?? (item.location === "local" ? item.thread : null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deletingLocal, setDeletingLocal] = useState(false)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const marquee = useTitleMarquee()
  const { prefs, toggleSubagentsCollapsed } = useSidebarPrefs()
  const activeSubagentId =
    useSearch({
      from: "/agents/$threadId",
      shouldThrow: false,
      select: (search) => search.subagent,
    }) ?? null

  const thread = item.location === "cloud" ? item.thread : null
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
  const subagentsCollapsed =
    prefs.collapseSubagentsByDefault !==
    prefs.collapsedSubagentKeys.includes(item.key)
  const rowIsActive =
    (isActive && (!activeSubagent || subagentsCollapsed)) ||
    (Boolean(activeWorkerId) && !workersExpanded)
  const source =
    item.source && item.source !== "dashboard" ? SOURCE_META[item.source] : null
  const SourceIcon = source?.icon
  // Strictly an unread marker, not a "finished" one: any thread the user has
  // not opened since its latest run shows the dot. The focused thread is being
  // read right now, so it never does — derived rather than left to the
  // optimistic cache patch, which a list refetch can overwrite.
  const unread = !item.viewed && !isActive
  const isDeleting =
    deletingLocal ||
    (item.location === "cloud" &&
      deleteThread.isPending &&
      deleteThread.variables === item.id)

  const markViewed = () => {
    if (item.reviewPage) markReviewViewed(queryClient, item.reviewPage, item.id)
    else if (item.location === "cloud")
      markAgentThreadViewed(queryClient, item.id)
    else markLocalViewed(item.id)
  }

  // Covers every way a row becomes active — click, command palette, keyboard
  // nav, browser back — not just the click handler below.
  useEffect(() => {
    if (isActive && !item.viewed) markViewed()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, item.viewed])

  const onConfirmDelete = async () => {
    if (isDeleting) return
    if (item.location === "cloud") {
      deleteThread.mutate(item.id, { onSuccess: () => setDeleteOpen(false) })
      return
    }
    setDeletingLocal(true)
    try {
      const deleted =
        (await window.openSweDesktop?.deleteLegacyLocalThread(item.id)) ?? false
      if (!deleted) throw new Error("Local Open SWE thread not found")
      onDeleteLocal(item.id)
      setDeleteOpen(false)
      if (isActive) {
        onNavigate?.()
        void navigate({ to: "/agents" })
      }
    } catch (error) {
      reportError({ title: "Couldn't delete thread", error })
    }
    setDeletingLocal(false)
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
    if (contextMenuOpen) {
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

      <span className="flex shrink-0 items-center gap-1.5 group-hover/row:hidden">
        {item.status === "error" && <ErrorIndicator label="Thread error" />}
        {thread?.automationActionPosted && (
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
    paddingLeft:
      hasSubagents || workers.length > 0
        ? indent
          ? "pl-4"
          : "pl-2"
        : indent
          ? "pl-6"
          : "pl-2.5",
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
      className={rowClassName}
    >
      {rowContent}
    </Link>
  ) : item.location === "cloud" ? (
    <Link
      to={chat.thread}
      params={{ threadId: item.id }}
      onClick={handleNavigate}
      onKeyDown={openContextMenuFromKeyboard}
      className={rowClassName}
    >
      {rowContent}
    </Link>
  ) : (
    <Link
      to="/agents/local/$sessionId"
      params={{ sessionId: item.id }}
      onClick={handleNavigate}
      onKeyDown={openContextMenuFromKeyboard}
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
            />
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="min-w-[10rem]">
          <ThreadMenuItems
            menu="context"
            thread={thread}
            localThread={item.location === "local" ? item.thread : undefined}
            pinned={pinned}
            archived={archived}
            isDeleting={isDeleting}
            onTogglePin={onTogglePin}
            onToggleArchived={onToggleArchived}
            onDelete={() => setDeleteOpen(true)}
          />
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
        onConfirm={() => void onConfirmDelete()}
        detail={
          !worktreeThread
            ? undefined
            : worktreeThread.ownedWorktrees?.length
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
            : "pl-16"
          : indent
            ? "pl-13.5"
            : "pl-11.5",
        archived: false,
      })}
    >
      <SidebarRowTitle marquee={marquee} title={subagent.title} />
      <span className="flex shrink-0 items-center gap-1.5">
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
}: {
  trigger: React.ReactElement
  card: React.ReactNode
}) {
  return (
    <HoverCard openDelay={500} closeDelay={100}>
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
  const onAMac = item.location === "local" || runsOnAMac(item.thread)
  const LocationIcon = onAMac ? LaptopRegularIcon : CloudIcon
  const locationLabel = onAMac ? "This Mac" : "Cloud"

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1 text-xs font-medium text-primary">
          {item.title}
        </span>
        {item.location === "cloud" && item.thread.visibility === "private" && (
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
        <span className="mt-px shrink-0 text-[11px] text-secondary">
          {compactAge(item.updatedAt)}
        </span>
      </div>
      {item.repoLabel && (
        <div className="flex min-w-0 items-center gap-1.5 text-secondary">
          <FolderIcon size={ICON_SIZE} weight="regular" className="shrink-0" />
          <span className="min-w-0 truncate text-xxs">{item.repoLabel}</span>
        </div>
      )}
      {item.pr && (
        <a
          href={item.pr.url}
          target="_blank"
          rel="noreferrer"
          onClick={(event) => event.stopPropagation()}
          className="pointer-events-auto -mx-1 flex min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-secondary hover:bg-surface-level-1-hover hover:text-primary"
        >
          <PullRequestIcon state={item.pr.state} live={live} />
          <span className="min-w-0 truncate text-xxs">{item.pr.title}</span>
        </a>
      )}
    </div>
  )
}

function SubagentHoverCard({ subagent }: { subagent: AgentSubagentSummary }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-start gap-2">
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
        <span className="mt-px shrink-0 text-[11px] text-secondary">
          {compactAge(subagent.endedAt ?? subagent.startedAt)}
        </span>
      </div>
      <div className="flex min-w-0 items-center gap-1.5 text-secondary">
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
  const subagentsCollapsed =
    prefs.collapseSubagentsByDefault !==
    prefs.collapsedSubagentKeys.includes(`cloud:${worker.id}`)
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
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-secondary">
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
