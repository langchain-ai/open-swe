import { Link, useNavigate } from "@tanstack/react-router"
import {
  ArchiveIcon,
  ArrowCounterClockwiseIcon,
  BookOpenTextIcon,
  CalendarBlankIcon,
  ChatCircleIcon,
  FolderIcon,
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
import type { AgentSource } from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import type { PrState } from "@/components/PrState"
import { PrStateIcon } from "@/components/PrState"
import {
  ContextMenu,
  ContextMenuPopup,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  HoverCard,
  HoverCardPopup,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Spinner } from "@/components/ui/spinner"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { useMarkLocalThreadViewed } from "@/features/agents/lib/desktopLocal"
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

type Icon = ComponentType<SVGProps<SVGSVGElement>>

const SOURCE_META: Record<AgentSource, { icon: Icon; label: string }> = {
  dashboard: { icon: ChatCircleIcon, label: "Started from the dashboard" },
  github: { icon: IoLogoGithub, label: "Triggered from GitHub" },
  slack: { icon: IoLogoSlack, label: "Triggered from Slack" },
  linear: { icon: SiLinear, label: "Triggered from Linear" },
  schedule: { icon: CalendarBlankIcon, label: "Triggered from a schedule" },
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
  return (
    <span className={cn("relative flex shrink-0", className)}>
      <PrStateIcon state={live?.state ?? state} />
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
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const marquee = useTitleMarquee()

  const thread = item.location === "cloud" ? item.thread : null
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
    setDeleteError(null)
    try {
      const deleted =
        (await window.openSweDesktop?.deleteLocalThread(item.id)) ?? false
      if (deleted) {
        onDeleteLocal(item.id)
        setDeleteOpen(false)
        if (isActive) {
          onNavigate?.()
          void navigate({ to: "/agents" })
        }
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

  const rowContent = (
    <>
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

      <span className="flex shrink-0 items-center gap-1.5 group-focus-within/row:hidden group-hover/row:hidden">
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
          <Spinner
            className="text-muted-foreground"
            aria-label="Thread running"
          />
        ) : unread ? (
          <span
            className="size-2 rounded-full bg-primary"
            aria-label="Unread thread"
          />
        ) : null}
      </span>

      <span className="-mr-[3px] hidden shrink-0 items-center gap-0.5 group-focus-within/row:flex group-hover/row:flex">
        <TooltipIconButton
          label={pinned ? "Unpin thread" : "Pin thread"}
          tooltip={pinned ? "Unpin" : "Pin"}
          size="icon-xs"
          onClick={onPinClick}
          className="rounded text-muted-foreground/80 hover:bg-accent"
        >
          {pinned ? (
            <PushPinSlashIcon className="size-3.5" />
          ) : (
            <PushPinIcon className="size-3.5" />
          )}
        </TooltipIconButton>
        <TooltipIconButton
          label={archived ? "Unarchive thread" : "Archive thread"}
          tooltip={archived ? "Unarchive" : "Archive"}
          size="icon-xs"
          onClick={onArchiveClick}
          className="rounded text-muted-foreground/80 hover:bg-accent"
        >
          {archived ? (
            <ArrowCounterClockwiseIcon className="size-3.5" />
          ) : (
            <ArchiveIcon className="size-3.5" />
          )}
        </TooltipIconButton>
      </span>
    </>
  )

  const rowClassName = cn(
    "flex items-center gap-2 rounded-lg pr-2.5 transition-colors",
    indent ? "pl-6" : "pl-2.5",
    // Only ever on screen while "Show archived" is on; without this an
    // archived row is indistinguishable from a live one.
    archived && "opacity-55",
    compact ? "h-7 gap-1.5" : "h-8",
    "text-foreground",
    isActive ? "bg-accent" : "group-hover/row:bg-sidebar-row-hover"
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
      <ContextMenu onOpenChange={setContextMenuOpen}>
        <ContextMenuTrigger
          className={cn(
            "group/row relative mb-0.5",
            isDeleting && "opacity-50"
          )}
          onMouseEnter={marquee.measure}
          onMouseLeave={marquee.reset}
        >
          <HoverCard>
            <HoverCardTrigger render={link}>{rowContent}</HoverCardTrigger>
            <HoverCardPopup
              side="right"
              align="start"
              sideOffset={8}
              className="w-auto max-w-80 shadow-xl shadow-black/25 [--dropdown-glass-background:var(--sidebar)]"
            >
              <ThreadHoverCard item={item} live={live} />
            </HoverCardPopup>
          </HoverCard>
        </ContextMenuTrigger>
        <ContextMenuPopup>
          <ThreadMenuItems
            thread={thread}
            localThread={item.location === "local" ? item.thread : undefined}
            pinned={pinned}
            archived={archived}
            isDeleting={isDeleting}
            onTogglePin={onTogglePin}
            onToggleArchived={onToggleArchived}
            onDelete={() => setDeleteOpen(true)}
          />
        </ContextMenuPopup>
      </ContextMenu>
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={(open) => {
          setDeleteOpen(open)
          if (!open) setDeleteError(null)
        }}
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
        error={deleteError}
      />
    </>
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
          className="-mx-1 flex min-w-0 items-center gap-1.5 rounded-md px-1 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <PullRequestIcon state={item.pr.state} live={live} />
          <span className="min-w-0 truncate text-[12px]">{item.pr.title}</span>
        </a>
      )}
    </div>
  )
}
