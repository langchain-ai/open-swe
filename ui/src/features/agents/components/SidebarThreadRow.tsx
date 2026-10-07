import { useEffect, useState } from "react"
import type { MouseEvent, ReactElement, SyntheticEvent } from "react"
import { Link, useNavigate, useSearch } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { SidebarTreeRow } from "@langchain/gtm-platform-design-system/patterns/sidebar-tree"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
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
import { FadeText } from "@langchain/gtm-platform-design-system/ui/fade-text"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"

import type { Glyph } from "@/components/glyphs"
import type { PullRequestSnapshot } from "@/features/agents/lib/api"
import type {
  AgentSource,
  AgentSubagentSummary,
  AgentThread,
} from "@/features/agents/lib/types"
import type { SidebarThreadItem } from "@/features/agents/lib/sidebarThreads"
import {
  AlertTriangle,
  BookOpen,
  Calendar,
  CheckCircle,
  ChevronRight,
  Ellipsis,
  GitHub,
  GitMerge,
  GitPullRequest,
  PushPin,
} from "@/components/glyphs"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { ThreadMenuItems } from "@/features/agents/components/ThreadMenuItems"
import { useLocalThread } from "@/features/agents/lib/desktopLocal"
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
import { useChatRoutes } from "@/lib/chatRoutes"

type PrState = NonNullable<AgentThread["pr"]>["state"]
type ExternalSource = Exclude<AgentSource, "dashboard">

const SOURCE_LABEL: Record<ExternalSource, string> = {
  github: "Triggered from GitHub",
  slack: "Triggered from Slack",
  linear: "Triggered from Linear",
  schedule: "Triggered from a schedule",
}

const PR_STATE_META: Record<
  PrState,
  { icon: Glyph; label: string; className: string }
> = {
  draft: {
    icon: GitPullRequest,
    label: "Draft pull request",
    className: "text-ink-subtle",
  },
  open: {
    icon: GitPullRequest,
    label: "Open pull request",
    className: "text-positive",
  },
  merged: {
    icon: GitMerge,
    label: "Merged pull request",
    className: "text-merged",
  },
  closed: {
    icon: GitPullRequest,
    label: "Closed pull request",
    className: "text-risk",
  },
}

const WORKTREE_DELETE_DETAIL =
  "This deletes the worktree Open SWE created for it, including any uncommitted changes in it. Its branch and commits are kept."
const LOCAL_DELETE_DETAIL =
  "This removes its history but does not revert changes made to your repository."

/*
 * The anchor's own box is only the title, so a pseudo-element stretches the
 * click target over the whole row; the controls beside it sit above it.
 */
const ROW_LINK_CLASS =
  "min-w-0 flex-1 outline-none after:absolute after:inset-0 after:rounded-compact focus-visible:after:ring-2 focus-visible:after:ring-primary"
const ABOVE_ROW_LINK_CLASS = "relative z-10"

/** Codex-style compact age ("17m", "3h", "2d") for the row's trailing slot. */
function compactAge(timestamp: number): string {
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000))
  if (minutes < 1) return "now"
  if (minutes < 60) return `${minutes}m`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.round(hours / 24)
  return days < 7 ? `${days}d` : `${Math.round(days / 7)}w`
}

/** Running wins; otherwise the blue dot means unread; otherwise a failure keeps its mark. */
function ThreadStatusMark({
  running,
  unread,
  failed,
  runningLabel,
  failedLabel,
}: {
  running: boolean
  unread: boolean
  failed: boolean
  runningLabel: string
  failedLabel: string
}) {
  if (running)
    return (
      <Spinner size="sm" label={runningLabel} className="text-ink-subtle" />
    )
  if (unread)
    return (
      <Box
        render={<span />}
        role="img"
        aria-label="Unread thread"
        className="size-1.5 shrink-0 rounded-full bg-primary"
      />
    )
  if (failed)
    return (
      <Icon
        icon={AlertTriangle}
        size="sm"
        label={failedLabel}
        className="text-attention"
      />
    )
  return null
}

/** Where the thread came from, in the row's leading lane. Brand marks sit in a 16px muted well. */
function ThreadOrigin({ source }: { source: ExternalSource }) {
  const label = SOURCE_LABEL[source]
  if (source === "slack" || source === "linear")
    return (
      <Box
        render={<span />}
        role="img"
        aria-label={label}
        title={label}
        className="inline-flex size-4 shrink-0 items-center justify-center rounded-tick border border-line bg-muted"
      >
        <ProviderLogo provider={source} className="size-3" />
      </Box>
    )
  return (
    <Icon
      icon={source === "github" ? GitHub : Calendar}
      size="sm"
      label={label}
      className="text-ink-subtle"
    />
  )
}

function PullRequestMark({
  state,
  live,
}: {
  state: PrState
  live?: PullRequestSnapshot
}) {
  // Thread metadata records the state the PR had when it was opened; live
  // truth wins so a merged PR stops rendering as open.
  const meta = PR_STATE_META[live?.state ?? state]
  return (
    <Box
      render={<span />}
      title={meta.label}
      className="relative flex shrink-0"
    >
      <Icon
        icon={meta.icon}
        size="sm"
        label={meta.label}
        className={meta.className}
      />
      {live?.checks === "failing" && (
        <Box
          render={<span />}
          role="img"
          aria-label="Checks failing"
          className="absolute -right-0.5 -bottom-0.5 size-1.5 rounded-full bg-risk ring-2 ring-sidebar"
        />
      )}
    </Box>
  )
}

function useThreadRowLink(item: SidebarThreadItem): ReactElement {
  const chat = useChatRoutes()
  const review = item.reviewPage
  if (review)
    return (
      <Link
        {...reviewPageRoute(review)}
        onClick={() => noteReviewOpenedFromSidebar(review)}
      />
    )
  if (item.location === "cloud")
    return <Link to={chat.thread} params={{ threadId: item.id }} />
  return <Link to="/agents/local/$sessionId" params={{ sessionId: item.id }} />
}

/**
 * One thread in the rail: a link named by its title, status on the row, and a
 * trailing slot that shows the age at rest and the thread's menu on hover or
 * focus. Right-click (or Shift+F10) opens the same menu.
 */
export function SidebarThreadRow({
  item,
  isActive,
  pinned,
  archived,
  live,
  compact = false,
  onDeleteLocal,
  onTogglePin,
  onToggleArchived,
}: {
  item: SidebarThreadItem
  isActive: boolean
  pinned: boolean
  archived: boolean
  live?: PullRequestSnapshot
  /** The "Compact rows" preference: the 28px rung instead of 32. */
  compact?: boolean
  onDeleteLocal: (threadId?: string) => void
  onTogglePin: () => void
  onToggleArchived: () => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const markLocalViewed = useMarkLegacyLocalThreadViewed()
  const deleteThread = useDeleteAgentThread()
  const worktreeThread =
    useLocalThread(item.id) ?? (item.location === "local" ? item.thread : null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deletingLocal, setDeletingLocal] = useState(false)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const { prefs, toggleSubagentsCollapsed } = useSidebarPrefs()
  const activeSubagentId =
    useSearch({
      from: "/agents/$threadId",
      shouldThrow: false,
      select: (search) => search.subagent,
    }) ?? null
  const link = useThreadRowLink(item)

  const thread = item.location === "cloud" ? item.thread : null
  const subagents = (item.subagents ?? []).filter(
    (subagent) => subagent.status !== "completed"
  )
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
  const rowIsActive = isActive && (!activeSubagent || subagentsCollapsed)
  const source = item.source && item.source !== "dashboard" ? item.source : null
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

  const confirmDelete = async () => {
    if (item.location === "cloud") {
      await deleteThread.mutateAsync(item.id)
      return
    }
    setDeletingLocal(true)
    try {
      const deleted =
        (await window.openSweDesktop?.deleteLegacyLocalThread(item.id)) ?? false
      if (!deleted) throw new Error("Local Open SWE thread not found")
      onDeleteLocal(item.id)
      if (isActive) void navigate({ to: "/agents" })
    } finally {
      setDeletingLocal(false)
    }
  }

  const handleNavigate = (event: MouseEvent<HTMLElement>) => {
    if (contextMenuOpen) {
      event.preventDefault()
      return
    }
    markViewed()
  }

  const onToggleSubagents = (event: SyntheticEvent) => {
    event.preventDefault()
    event.stopPropagation()
    toggleSubagentsCollapsed(item.key)
  }

  const menuItems = (
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
  )
  const age = compactAge(item.updatedAt)

  return (
    <>
      <ContextMenu onOpenChange={setContextMenuOpen}>
        <ContextMenuTrigger className={cn(isDeleting && "opacity-50")}>
          <SidebarTreeRow
            selected={rowIsActive}
            multiline={false}
            className={cn(
              "group/thread-row",
              compact && "min-h-control-sm py-1"
            )}
          >
            <Inline gap="sm" align="center" className="min-w-0 pr-7">
              {hasSubagents && (
                <Box
                  render={<button type="button" />}
                  aria-expanded={!subagentsCollapsed}
                  aria-label={
                    subagentsCollapsed ? "Show subagents" : "Hide subagents"
                  }
                  title={
                    subagentsCollapsed ? "Show subagents" : "Hide subagents"
                  }
                  onClick={onToggleSubagents}
                  className={cn(
                    ABOVE_ROW_LINK_CLASS,
                    "-my-1 inline-flex size-4 shrink-0 items-center justify-center rounded-tick text-ink-subtle outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary"
                  )}
                >
                  <Icon
                    icon={ChevronRight}
                    size="sm"
                    className={cn(
                      "transition-[rotate] duration-fast ease-out-quint motion-reduce:transition-none",
                      !subagentsCollapsed && "rotate-90"
                    )}
                  />
                </Box>
              )}
              {pinned && (
                <Icon
                  icon={PushPin}
                  size="sm"
                  label="Pinned"
                  className="text-ink-subtle"
                />
              )}
              {source && <ThreadOrigin source={source} />}
              <Box
                render={link}
                onClick={handleNavigate}
                className={ROW_LINK_CLASS}
              >
                <FadeText
                  render={<span />}
                  lines={1}
                  className={cn(
                    "text-label font-medium whitespace-nowrap",
                    // Only on screen while "Show archived" is on; without this
                    // an archived row is indistinguishable from a live one.
                    archived ? "text-ink-subtle" : "text-ink"
                  )}
                >
                  {item.title}
                </FadeText>
              </Box>
              {thread?.automationActionPosted && (
                <Icon
                  icon={CheckCircle}
                  size="sm"
                  label="Action posted to Slack"
                  className="text-positive"
                />
              )}
              {item.reviewPage ? (
                <Icon
                  icon={BookOpen}
                  size="sm"
                  label="Pull request review"
                  className="text-ink-subtle"
                />
              ) : (
                item.pr && <PullRequestMark state={item.pr.state} live={live} />
              )}
              <ThreadStatusMark
                running={item.status === "running"}
                unread={unread}
                failed={item.status === "error"}
                runningLabel="Thread running"
                failedLabel="Thread error"
              />
            </Inline>
            <Box
              data-slot="thread-row-actions"
              className={cn(
                ABOVE_ROW_LINK_CLASS,
                "absolute inset-y-0 right-1 flex w-7 items-center justify-center"
              )}
            >
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Actions for ${item.title}`}
                    />
                  }
                >
                  <Box className="grid place-items-center">
                    <Box
                      render={<span />}
                      className="col-start-1 row-start-1 text-meta whitespace-nowrap text-ink-subtle tabular-nums group-focus-within/thread-row:opacity-0 group-hover/thread-row:opacity-0 group-has-[[data-popup-open]]/thread-row:opacity-0 pointer-coarse:opacity-0"
                    >
                      {age}
                    </Box>
                    <Box
                      render={<span />}
                      className="col-start-1 row-start-1 opacity-0 group-focus-within/thread-row:opacity-100 group-hover/thread-row:opacity-100 group-has-[[data-popup-open]]/thread-row:opacity-100 pointer-coarse:opacity-100"
                    >
                      <Icon icon={Ellipsis} size="sm" />
                    </Box>
                  </Box>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  side="right"
                  align="start"
                  sideOffset={6}
                  className="min-w-48"
                >
                  {menuItems}
                </DropdownMenuContent>
              </DropdownMenu>
            </Box>
          </SidebarTreeRow>
        </ContextMenuTrigger>
        <ContextMenuContent className="min-w-48">
          {menuItems}
        </ContextMenuContent>
      </ContextMenu>
      {hasSubagents && !subagentsCollapsed && (
        <Stack
          render={<ul aria-label={`Subagents of ${item.title}`} />}
          gap="none"
          className="ml-4 gap-0.5 border-l border-line-strong pl-2"
        >
          {subagents.map((subagent) => (
            <SidebarSubagentRow
              key={subagent.toolCallId}
              threadId={item.id}
              subagent={subagent}
              isActive={activeSubagent === subagent.toolCallId}
            />
          ))}
        </Stack>
      )}
      {deleteOpen && (
        <DeleteThreadDialog
          threadTitle={item.title}
          onConfirm={confirmDelete}
          onDismiss={() => setDeleteOpen(false)}
          detail={
            !worktreeThread
              ? undefined
              : worktreeThread.ownedWorktrees?.length
                ? WORKTREE_DELETE_DETAIL
                : LOCAL_DELETE_DETAIL
          }
        />
      )}
    </>
  )
}

/**
 * A subagent listed under the thread that spawned it, on the child rung.
 * Opening it shows the subagent's own transcript on the thread page; the
 * parent row's selection moves here while it does.
 */
function SidebarSubagentRow({
  threadId,
  subagent,
  isActive,
}: {
  threadId: string
  subagent: AgentSubagentSummary
  isActive: boolean
}) {
  return (
    <Box render={<li />}>
      <SidebarTreeRow
        selected={isActive}
        multiline={false}
        className="min-h-control-sm py-1"
      >
        <Inline gap="sm" align="center" className="min-w-0">
          <Box
            render={
              <Link
                to="/agents/$threadId"
                params={{ threadId }}
                search={{ subagent: subagent.toolCallId }}
              />
            }
            title={subagent.title}
            className={ROW_LINK_CLASS}
          >
            <FadeText
              render={<span />}
              lines={1}
              className="text-label whitespace-nowrap text-ink-muted"
            >
              {subagent.title}
            </FadeText>
          </Box>
          <ThreadStatusMark
            running={subagent.status === "in_progress"}
            unread={false}
            failed={subagent.status === "error"}
            runningLabel="Subagent running"
            failedLabel="Subagent failed"
          />
        </Inline>
      </SidebarTreeRow>
    </Box>
  )
}

/**
 * The 48px icon rail's thread: a scannable status dot, the title on a right
 * tooltip, and still a link to the thread.
 */
export function SidebarThreadDot({
  item,
  isActive,
}: {
  item: SidebarThreadItem
  isActive: boolean
}) {
  const link = useThreadRowLink(item)
  const unread = !item.viewed && !isActive
  const marked = item.status === "running" || unread || item.status === "error"
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            render={link}
            nativeButton={false}
            aria-label={item.title}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "w-full",
              isActive && "bg-selected hover:bg-selected"
            )}
          />
        }
      >
        {marked ? (
          <ThreadStatusMark
            running={item.status === "running"}
            unread={unread}
            failed={item.status === "error"}
            runningLabel="Thread running"
            failedLabel="Thread error"
          />
        ) : (
          <Box
            render={<span />}
            aria-hidden
            className="size-1.5 rounded-full bg-ink-subtle"
          />
        )}
      </TooltipTrigger>
      <TooltipContent side="right">{item.title}</TooltipContent>
    </Tooltip>
  )
}
