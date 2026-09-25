import { ContextMenu } from "@base-ui/react/context-menu"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  ArchiveIcon,
  ArrowCounterClockwiseIcon,
  BookOpenTextIcon,
  CalendarBlankIcon,
  CaretDownIcon,
  CaretRightIcon,
  ChatCircleIcon,
  CircleNotchIcon,
  FolderIcon,
  GitMergeIcon,
  GitPullRequestIcon,
  LockIcon,
  PushPinIcon,
  PushPinSlashIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react"
import {
  IoCloudOutline,
  IoLaptopOutline,
  IoLogoGithub,
  IoLogoSlack,
} from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { useEffect, useRef, useState } from "react"
import type { ComponentType, SVGProps } from "react"

import type { PullRequestSnapshot } from "@/features/agents/lib/api"
import type {
  AgentSource,
  AgentSubagentSummary,
  AgentThread,
} from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { useMarkLocalThreadViewed } from "@/features/agents/lib/desktopLocal"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { useActiveSubagentId } from "@/features/agents/lib/useActiveSubagentId"
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
    className: "text-merged-foreground",
  },
  closed: {
    icon: GitPullRequestIcon,
    label: "Closed pull request",
    className: "text-destructive",
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
        className={cn("size-3.5", meta.className)}
        aria-label={meta.label}
      />
      {live?.checks === "failing" && (
        <span
          className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-destructive ring-2 ring-sidebar"
          aria-label="Checks failing"
        />
      )}
    </span>
  )
}

export function SidebarThreadRow({
  item,
  isActive,
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
  const markLocalViewed = useMarkLocalThreadViewed()
  const deleteThread = useDeleteAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deletingLocal, setDeletingLocal] = useState(false)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const marquee = useTitleMarquee()
  const { prefs, toggleSubagentsCollapsed } = useSidebarPrefs()
  const activeSubagentId = useActiveSubagentId()

  const thread = item.location === "cloud" ? item.thread : null
  const subagents = item.subagents ?? []
  const hasSubagents = subagents.length > 0
  // A sub-thread is on screen: it takes the highlight, and stays visible even
  // when the fold is closed so the row that is open is never hidden.
  const activeSubagent =
    isActive &&
    activeSubagentId &&
    subagents.some((subagent) => subagent.toolCallId === activeSubagentId)
      ? activeSubagentId
      : null
  const subagentsCollapsed =
    !activeSubagent && prefs.collapsedSubagentKeys.includes(item.key)
  const rowIsActive = isActive && !activeSubagent
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
        (await window.openSweDesktop?.deleteLocalThread(item.id)) ?? false
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
          className="-ml-0.5 flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground/80 hover:bg-accent hover:text-foreground"
        >
          <SubagentCaret className="size-3" weight="bold" />
        </span>
      )}
      <span
        ref={marquee.viewport}
        className={cn(
          "sidebar-title-viewport relative min-w-0 flex-1 overflow-hidden",
          marquee.shift !== 0 && "sidebar-title-marquee-mask"
        )}
      >
        <span
          ref={marquee.text}
          className={cn(
            "block w-max text-sm whitespace-nowrap will-change-transform",
            marquee.shift !== 0 && "sidebar-title-marquee"
          )}
          style={
            {
              "--marquee-shift": `${marquee.shift}px`,
              "--marquee-duration": `${marquee.duration}s`,
            } as React.CSSProperties
          }
        >
          {item.title}
        </span>
      </span>

      <span className="flex shrink-0 items-center gap-1.5 group-hover/row:hidden">
        {item.status === "error" && (
          <WarningCircleIcon
            className="size-3.5 text-destructive"
            aria-label="Thread error"
          />
        )}
        {thread?.automationActionPosted && (
          <IoLogoSlack
            className="size-3.5 text-success-foreground"
            aria-label="Action posted to Slack"
          />
        )}
        {item.reviewPage ? (
          <BookOpenTextIcon
            className="size-3.5 text-muted-foreground/70"
            aria-label="Pull request review"
          />
        ) : (
          <>
            {source && SourceIcon && !item.pr && (
              <SourceIcon
                className="size-3.5 text-muted-foreground/70"
                aria-label={source.label}
              />
            )}
            {item.pr && <PullRequestIcon state={item.pr.state} live={live} />}
          </>
        )}
        {item.status === "running" ? (
          <CircleNotchIcon
            className="size-3.5 animate-spin text-muted-foreground"
            aria-label="Thread running"
          />
        ) : unread ? (
          <span
            className="size-2 rounded-full bg-primary"
            aria-label="Unread thread"
          />
        ) : null}
      </span>

      <span className="-mr-[3px] hidden shrink-0 items-center gap-0.5 group-hover/row:flex">
        <button
          type="button"
          aria-label={pinned ? "Unpin thread" : "Pin thread"}
          title={pinned ? "Unpin" : "Pin"}
          onClick={onPinClick}
          className="flex size-5 items-center justify-center rounded text-muted-foreground/80 hover:bg-accent hover:text-foreground"
        >
          {pinned ? (
            <PushPinSlashIcon className="size-3.5" />
          ) : (
            <PushPinIcon className="size-3.5" />
          )}
        </button>
        <button
          type="button"
          aria-label={archived ? "Unarchive thread" : "Archive thread"}
          title={archived ? "Unarchive" : "Archive"}
          onClick={onArchiveClick}
          className="flex size-5 items-center justify-center rounded text-muted-foreground/80 hover:bg-accent hover:text-foreground"
        >
          {archived ? (
            <ArrowCounterClockwiseIcon className="size-3.5" />
          ) : (
            <ArchiveIcon className="size-3.5" />
          )}
        </button>
      </span>
    </>
  )

  const rowClassName = cn(
    "flex items-center gap-2 rounded-lg pr-2.5 transition-colors",
    hasSubagents ? (indent ? "pl-4" : "pl-1") : indent ? "pl-6" : "pl-2.5",
    // Only ever on screen while "Show archived" is on; without this an
    // archived row is indistinguishable from a live one.
    archived && "opacity-55",
    compact ? "h-7 gap-1.5" : "h-8",
    "text-foreground",
    rowIsActive ? "bg-accent" : "group-hover/row:bg-sidebar-row-hover"
  )

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
    />
  ) : item.location === "cloud" ? (
    <Link
      to={chat.thread}
      params={{ threadId: item.id }}
      onClick={handleNavigate}
      onKeyDown={openContextMenuFromKeyboard}
      className={rowClassName}
    />
  ) : (
    <Link
      to="/agents/local/$sessionId"
      params={{ sessionId: item.id }}
      onClick={handleNavigate}
      onKeyDown={openContextMenuFromKeyboard}
      className={rowClassName}
    />
  )

  return (
    <>
      <ContextMenu.Root onOpenChange={setContextMenuOpen}>
        <ContextMenu.Trigger
          className={cn(
            "group/row relative mb-0.5",
            isDeleting && "opacity-50"
          )}
          onMouseEnter={marquee.measure}
          onMouseLeave={marquee.reset}
        >
          <Tooltip>
            <TooltipTrigger render={link}>{rowContent}</TooltipTrigger>
            <TooltipPopup
              variant="glass"
              side="right"
              align="start"
              sideOffset={8}
              className="pointer-events-auto max-w-80 rounded-xl p-3 [--dropdown-glass-background:var(--sidebar)]"
            >
              <ThreadHoverCard item={item} live={live} />
            </TooltipPopup>
          </Tooltip>
        </ContextMenu.Trigger>
        <ContextMenu.Portal>
          <ContextMenu.Positioner className="z-50 outline-none">
            <ContextMenu.Popup className="min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
              <ThreadMenuItems
                thread={thread}
                localThread={
                  item.location === "local" ? item.thread : undefined
                }
                pinned={pinned}
                archived={archived}
                isDeleting={isDeleting}
                onTogglePin={onTogglePin}
                onToggleArchived={onToggleArchived}
                onDelete={() => setDeleteOpen(true)}
              />
            </ContextMenu.Popup>
          </ContextMenu.Positioner>
        </ContextMenu.Portal>
      </ContextMenu.Root>
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
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={item.title}
        isDeleting={isDeleting}
        onConfirm={() => void onConfirmDelete()}
        detail={
          item.location !== "local"
            ? undefined
            : item.thread.ownedWorktrees?.length
              ? "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
              : "This removes its history but does not revert changes made to your repository."
        }
      />
    </>
  )
}

function subagentAge(startedAt: number): string {
  const age = compactAge(startedAt)
  return age === "now" ? "just now" : `${age} ago`
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
  compact,
  indent,
  onNavigate,
}: {
  threadId: string
  subagent: AgentSubagentSummary
  isActive: boolean
  compact: boolean
  indent: boolean
  onNavigate?: () => void
}) {
  return (
    <li className="group/row relative mb-0.5">
      <Link
        to="/agents/$threadId"
        params={{ threadId }}
        search={{ subagent: subagent.toolCallId }}
        onClick={() => onNavigate?.()}
        title={subagent.title}
        className={cn(
          "flex items-center gap-2 rounded-lg pr-2.5 text-foreground transition-colors",
          indent ? "pl-11" : "pl-7",
          compact ? "h-7 gap-1.5" : "py-1",
          isActive ? "bg-accent" : "hover:bg-sidebar-row-hover"
        )}
      >
        <span
          className="size-1 shrink-0 rounded-full bg-muted-foreground/50"
          aria-hidden
        />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm">{subagent.title}</span>
          {!compact && (
            <span className="truncate text-xs text-muted-foreground">
              {subagentAge(subagent.startedAt)}
            </span>
          )}
        </span>
        {subagent.status === "in_progress" ? (
          <CircleNotchIcon
            className="size-3.5 shrink-0 animate-spin text-muted-foreground"
            aria-label="Subagent running"
          />
        ) : subagent.status === "error" ? (
          <WarningCircleIcon
            className="size-3.5 shrink-0 text-destructive"
            aria-label="Subagent failed"
          />
        ) : null}
      </Link>
    </li>
  )
}

function ThreadHoverCard({
  item,
  live,
}: {
  item: SidebarThreadItem
  live?: PullRequestSnapshot
}) {
  const LocationIcon =
    item.location === "local" ? IoLaptopOutline : IoCloudOutline
  const locationLabel = item.location === "local" ? "This Mac" : "Cloud"

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1 text-[13px] font-medium text-foreground">
          {item.title}
        </span>
        {item.location === "cloud" && item.thread.visibility === "private" && (
          <LockIcon
            className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
            aria-label="Private thread"
          />
        )}
        <LocationIcon
          className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
          aria-label={locationLabel}
        />
        <span className="mt-px shrink-0 text-[11px] text-muted-foreground">
          {compactAge(item.updatedAt)}
        </span>
      </div>
      {item.repoLabel && (
        <div className="flex min-w-0 items-center gap-1.5 text-muted-foreground">
          <FolderIcon className="size-3.5 shrink-0" />
          <span className="min-w-0 truncate text-[12px]">{item.repoLabel}</span>
        </div>
      )}
      {item.pr && (
        <a
          href={item.pr.url}
          target="_blank"
          rel="noreferrer"
          onClick={(event) => event.stopPropagation()}
          className="pointer-events-auto -mx-1 flex min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <PullRequestIcon state={item.pr.state} live={live} />
          <span className="min-w-0 truncate text-[12px]">{item.pr.title}</span>
        </a>
      )}
    </div>
  )
}
