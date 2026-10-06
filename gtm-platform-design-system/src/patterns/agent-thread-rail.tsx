"use client";

/*
 * Rules for AgentThreadRail.
 *
 * Focus-mode rail (CORE 05/12): agent feature destinations (SidebarNav),
 * then list chrome (optional SearchInput + one Display menu), then
 * collapsible grouped threads. Thread management via DropdownMenu —
 * no private chrome, no Status/Project tabs, no standalone New thread button.
 *
 * Equal `sm` pad around PostureBar via `leading`. Features + list sit in a
 * flush-top stack — same rail rhythm as the product Home/Agent strip.
 */

import { memo, useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Circle,
  Clock,
  Ellipsis,
  FileText,
  Filter,
  Folder,
  FolderOpen,
  Globe,
  Layers,
  List,
  Pencil,
  PushPin,
  Trash2,
  type Glyph,
} from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";
import { HostBrandMark as LangSmithMark } from "../host";
import { ProviderLogo } from "../ui/provider-logos";
import { ScrollArea } from "../ui/scroll-area";
import { SearchInput } from "../ui/search-input";
import { Skeleton } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../ui/tooltip";

import { SidebarTreeGroup, SidebarTreeRow } from "./sidebar-tree";

import type { ProviderId } from "./provider-mark";
import { SidebarNav, SidebarNavItem } from "./sidebar-nav";

const AGENT_THREAD_RAIL_RULES: readonly string[] = [
  "In focus mode this rail replaces the navigation rail — it is not a third sidebar. Width follows AppShell's rail (246–400px, default 246).",
  "Agent features are real `SidebarNav` / `SidebarNavItem` rows (New thread, Scheduled, Skills with Zap, …) — same page rungs as the product rail. Mount via `features` with `SidebarNav flush`. No standalone New thread button; creation lives in the feature list or the composer.",
  "List chrome follows the narrow-rail law: ONE filter menu for grouping / ordering / show. It lives on the Conversations group header, not on a second CONVERSATIONS masthead above the groups. Standing `SearchInput` is enabled by the host through `onSearchChange`; never park Search + Filter + Group as three peers in a 246px row.",
  "The host defaults to projects when projects exist and restores the reader's chosen grouping. Origin grouping remains available; status is shown on each thread, never as a group. Running threads show a spinner. Otherwise, unread threads show a blue dot; read threads have no blue dot. Blocked or failed read threads retain an attention mark. Pinned conversations and pinned project disclosures share the first PINNED section, independently of grouping. Slack-origin rows use the same title-and-time line as web rows, plus a leading 16px Slack mark in a muted compact well. Never put a 24px ProviderMark well on the row: that grows the control rung.",
  "Read and unread titles use the same weight. A blue dot means unread, never completed. Opening a thread clears its dot; Mark as read or Mark as unread in the row menu changes it through the same principal-scoped label mutation. Explicitly marking the open thread unread keeps its dot until marked read or reopened.",
  "Drag a thread onto a project or No project to move it and clear its individual pin. Drop onto Pinned to pin it while retaining its project. Drops use one host-owned label update; row menus remain the keyboard alternative.",
  "Groups are collapsible. Non-project groups put the disclosure chevron to the right of the label. A project group uses its chosen identity glyph, or Folder closed and FolderOpen open when none was chosen, and never a chevron beside it. A section header is a category disclosure, not a destination.",
  "Project headings carry the project identity; other headings use a disclosure chevron. Status marks belong to individual threads, including pinned threads and the compact rail.",
  "Two row rungs, and the rung follows what the ROW renders rather than which group it sits in: 44px when the row actually has a second line, 32px when it is one line plus a relative time — the control rung, the same height as a nav row. A group's disposition (pinned / attention / project) decides whether a subtitle is ALLOWED, not whether one exists — taking the tall rung off the group alone puts a 44px box around 16px of text. Rows centre in whichever rung they take; `min-h-*` alone pins short content to the ceiling.",
  "Rows keep a fixed title width. The single trailing slot shows the date at rest and the action menu on hover, keyboard focus, or touch. Pinned threads always show a small leading thumbtack. Pin/Unpin lives in the explicit menu; there is no row preview card or hover-only pin button. The menu opens right and groups source links, organisation, and destructive actions. The scrollbar stays outside list controls.",
  "One chrome pad on PostureBar only (`padding=\"sm\"`, equal on all sides). Features + list sit in a flush-top stack — never a second root `p-2` / Separator under the feature list. Mount PostureBar via `leading`.",
  "Opening a thread from the dock uses the same list — one source of truth (platform decision 5.1).",
  "Thread history loads one server cursor page at a time as the reader reaches the end. Keep the explicit Load older conversations control as the keyboard and observer fallback. During title search, only that explicit control continues the bounded search; never preload every thread or its transcript.",
  "While the initial thread query is pending, keep headers and controls mounted and show skeleton rows in empty list bodies. Empty-state copy appears only after loading settles. Cached rows stay visible during background refresh; loading older pages keeps its separate end control.",
  "When AppShell collapses the rail (`compact`), features use `SidebarNavItem compact` and thread rows become icon-rail dots with the title on a right Tooltip — never clipped title text inside `w-12`.",
];

type ThreadRailGroupBy = "project" | "origin";
type ThreadRailSort = "recent" | "title" | "status";
type ThreadRailFilter = "all" | "unread" | "attention";

interface ThreadRailProject {
  id: string;
  label: string;
  pinned?: boolean;
  glyph?: Glyph;
  glyphClassName?: string;
}

interface ThreadRailItem {
  id: string;
  title: string;
  subtitle?: string;
  meta?: string;
  unread?: boolean;
  statusName?: "working" | "done" | "attention";
  provider?: ProviderId;
  sourceHref?: string;
  traceHref?: string;
  selected?: boolean;
  pinned?: boolean;
  /** The thread needs input or encountered an error. */
  attention?: boolean;
  projectId?: string | null;
  /** Optional thread summary supplied by the host. */
  summary?: string;
}

interface ThreadRailGroup {
  id: string;
  label: string;
  count: number;
  kind?: "status" | "project" | "origin";
  /** Project headings draw this identity glyph; status headings use a chevron. */
  glyph?: Glyph;
  glyphClassName?: string;
  projectId?: string;
  pinned?: boolean;
  items: readonly ThreadRailItem[];
  /** Pinned projects keep their own disclosure within the shared Pinned section. */
  projectGroups?: readonly ThreadRailGroup[];
}

/** Agent feature destination — same shape as a SidebarNav page. */
interface ThreadRailFeature {
  id: string;
  label: string;
  icon: Glyph;
  count?: number;
  selected?: boolean;
  href?: string;
  onSelect?: () => void;
}

type ThreadRailDropTarget =
  | { kind: "pinned" }
  | { kind: "project"; projectId: string | null };

interface ThreadRailDrag {
  item: ThreadRailItem | null;
  overId: string | null;
  start: (item: ThreadRailItem) => void;
  end: () => void;
  over: (id: string | null) => void;
  drop: (groupId: string, target: ThreadRailDropTarget) => void;
}

function threadDropTarget(group: ThreadRailGroup): ThreadRailDropTarget | null {
  if (group.id === "pinned") return { kind: "pinned" };
  if (group.kind === "project" && group.projectId) return { kind: "project", projectId: group.projectId };
  if (group.id === "no-project" || group.id === "threads") return { kind: "project", projectId: null };
  return null;
}

interface AgentThreadRailProps {
  /** One principal-scoped label update for a completed thread drag. */
  onDropThread?: (id: string, target: ThreadRailDropTarget) => void;
  groups: readonly ThreadRailGroup[];
  /**
   * Agent feature pages (New task, Scheduled, Skills, …).
   * Rendered with SidebarNav so they match the product rail, not a private list.
   */
  features?: readonly ThreadRailFeature[];
  /** Rail chrome above features — typically PostureBar. */
  leading?: ReactNode;
  /**
   * Icon-rail mode for AppShell `railCollapsed` (48px). Features go compact;
   * thread rows become title-labeled dots. Independent of group disclosure ids.
   */
  compact?: boolean;
  search?: string;
  onSearchChange?: (value: string) => void;
  onSelectThread?: (id: string) => void;
  /**
   * A row was pointed at or focused, a beat before it is opened.
   *
   * The rail never fetches: it reports the intent and the host decides whether
   * warming that thread is worth a request. Fired on hover and on focus, so a
   * keyboard walk down the list warms the same rows a mouse would.
   */
  onPrefetchThread?: (id: string) => void;
  /** Grouping owned by the Group control — not Tabs. */
  groupBy?: ThreadRailGroupBy;
  onGroupByChange?: (next: ThreadRailGroupBy) => void;
  sort?: ThreadRailSort;
  onSortChange?: (next: ThreadRailSort) => void;
  filter?: ThreadRailFilter;
  onFilterChange?: (next: ThreadRailFilter) => void;
  /**
   * Collapsed group ids. Omit for uncontrolled (all open by default).
   * Surfaces that restore open state from URL pass both props.
   */
  collapsedGroupIds?: readonly string[];
  onCollapsedGroupIdsChange?: (ids: readonly string[]) => void;
  projects?: readonly ThreadRailProject[];
  onPinThread?: (id: string, pinned: boolean) => void;
  onMarkThreadRead?: (id: string) => void;
  onMarkThreadUnread?: (id: string) => void;
  onArchiveThread?: (id: string) => void;
  onRenameThread?: (id: string) => void;
  /**
   * Delete the thread this row names.
   *
   * The rail reports the intent and draws no confirmation of its own. The
   * consequence and the final confirmation belong to the host.
   */
  onDeleteThread?: (id: string) => void;
  onMoveThread?: (id: string, projectId: string | null) => void;
  onPinProject?: (id: string, pinned: boolean) => void;
  /** Whether the server issued another opaque cursor for this list. */
  hasMore?: boolean;
  /** Whether the current thread query is pending without cached data. */
  loading?: boolean;
  /** Whether the next cursor page is currently being read. */
  loadingMore?: boolean;
  /** Search pages advance explicitly so an empty match page cannot drain all history. */
  autoLoadMore?: boolean;
  /** Ask the host to read the next cursor page. */
  onLoadMore?: () => void;
  footer?: ReactNode;
  className?: string;
}

function ThreadDestinationMenuItem({
  description,
  href,
  label,
  mark,
}: {
  description: string;
  href: string;
  label: string;
  mark: ReactNode;
}) {
  return (
    <DropdownMenuItem
      render={<a href={href} target="_blank" rel="noopener noreferrer" />}
      className="items-start bg-muted py-2"
    >
      <IconWell>{mark}</IconWell>
      <Stack gap="xs" className="min-w-0">
        <Box render={<span />} className="font-medium text-ink">
          {label}
        </Box>
        <Box render={<span />} className="text-meta text-ink-subtle">
          {description}
        </Box>
      </Stack>
    </DropdownMenuItem>
  );
}

function AgentThreadMenu({
  placement = "row",
  children,
  item,
  projects,
  onArchiveThread,
  onDeleteThread,
  onMoveThread,
  onPinThread,
  onMarkThreadRead,
  onMarkThreadUnread,
  onRenameThread,
}: {
  placement?: "row" | "toolbar";
  children?: ReactNode;
  item: ThreadRailItem;
  projects?: readonly ThreadRailProject[];
  onArchiveThread?: (id: string) => void;
  onDeleteThread?: (id: string) => void;
  onMoveThread?: (id: string, projectId: string | null) => void;
  onPinThread?: (id: string, pinned: boolean) => void;
  onMarkThreadRead?: (id: string) => void;
  onMarkThreadUnread?: (id: string) => void;
  onRenameThread?: (id: string) => void;
}) {
  const hasOpenActions =
    (item.sourceHref !== undefined && item.provider === "slack") ||
    item.traceHref !== undefined;
  const hasOrganizeActions =
    (item.unread === true && onMarkThreadRead !== undefined) ||
    (item.unread !== true && onMarkThreadUnread !== undefined) ||
    onPinThread !== undefined ||
    onRenameThread !== undefined ||
    onMoveThread !== undefined;
  const hasDestructiveActions =
    onArchiveThread !== undefined || onDeleteThread !== undefined;
  const manageable =
    hasOpenActions || hasOrganizeActions || hasDestructiveActions || children != null;

  if (!manageable) {
    return item.meta ? <Box render={<span />} className="text-meta text-ink-subtle tabular-nums">{item.meta}</Box> : null;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size={placement === "toolbar" ? "icon" : "icon-sm"}
            aria-label={placement === "toolbar" ? "Thread actions" : `Actions for ${item.title}`}
            title={placement === "toolbar" ? "Thread actions" : undefined}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          />
        }
      >
        <Box className="grid place-items-center">
          {placement === "row" && item.meta ? (
            <Box render={<span />} className="col-start-1 row-start-1 whitespace-nowrap text-meta text-ink-subtle tabular-nums group-hover/thread-row:opacity-0 group-focus-within/thread-row:opacity-0 group-has-[[data-popup-open]]/thread-row:opacity-0 pointer-coarse:opacity-0">
              {item.meta}
            </Box>
          ) : null}
          <Box render={<span />} className={cn(
            "col-start-1 row-start-1 group-hover/thread-row:opacity-100 group-focus-within/thread-row:opacity-100 group-has-[[data-popup-open]]/thread-row:opacity-100 pointer-coarse:opacity-100",
            placement === "row" && item.meta && "opacity-0"
          )}>
            <Icon icon={Ellipsis} size="sm" />
          </Box>
        </Box>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side={placement === "toolbar" ? "bottom" : "right"}
        align="start"
        sideOffset={6}
        className="min-w-64"
      >
        {hasOpenActions ? (
          <DropdownMenuGroup className="space-y-1">
            <DropdownMenuLabel>Open</DropdownMenuLabel>
            {item.sourceHref && item.provider === "slack" ? (
              <ThreadDestinationMenuItem
                href={item.sourceHref}
                label="View in Slack"
                description="Open the original conversation."
                mark={<ProviderLogo provider="slack" />}
              />
            ) : null}
            {item.traceHref ? (
              <ThreadDestinationMenuItem
                href={item.traceHref}
                label="View trace"
                description="Inspect this thread in LangSmith."
                mark={<LangSmithMark className="text-primary" />}
              />
            ) : null}
          </DropdownMenuGroup>
        ) : null}
        {hasOpenActions && hasOrganizeActions ? (
          <DropdownMenuSeparator />
        ) : null}
        {hasOrganizeActions ? (
          <DropdownMenuGroup>
            <DropdownMenuLabel>Organize</DropdownMenuLabel>
            {!item.unread && onMarkThreadUnread ? (
              <DropdownMenuItem onClick={() => onMarkThreadUnread(item.id)}>
                <Icon icon={Circle} size="sm" />
                Mark as unread
              </DropdownMenuItem>
            ) : null}
            {item.unread && onMarkThreadRead ? (
              <DropdownMenuItem onClick={() => onMarkThreadRead(item.id)}>
                <Icon icon={Check} size="sm" />
                Mark as read
              </DropdownMenuItem>
            ) : null}
          {onPinThread ? (
            <DropdownMenuItem
              onClick={() => onPinThread(item.id, !item.pinned)}
            >
              <Icon icon={PushPin} size="md" />
              {item.pinned ? "Unpin" : "Pin"}
            </DropdownMenuItem>
          ) : null}
          {onRenameThread ? (
            <DropdownMenuItem onClick={() => onRenameThread(item.id)}>
              <Icon icon={Pencil} size="sm" />
              Rename
            </DropdownMenuItem>
          ) : null}
          {onMoveThread && projects && projects.length > 0 ? (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Icon icon={FolderOpen} size="sm" />
                Move to project
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuItem onClick={() => onMoveThread(item.id, null)}>
                  No project
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {projects.map((project) => (
                  <DropdownMenuItem
                    key={project.id}
                    onClick={() => onMoveThread(item.id, project.id)}
                  >
                    <Icon
                      icon={project.glyph ?? Folder}
                      size="sm"
                      className={project.glyphClassName ?? "text-ink-subtle"}
                    />
                    {project.label}
                    {item.projectId === project.id ? (
                      <Box
                        render={<span />}
                        className="ml-auto text-meta text-ink-subtle"
                      >
                        Current
                      </Box>
                    ) : null}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          ) : null}
          </DropdownMenuGroup>
        ) : null}
        {children != null ? (
          <>
            {hasOpenActions || hasOrganizeActions ? <DropdownMenuSeparator /> : null}
            {children}
          </>
        ) : null}
        {/*
         * The verbs that take the thread off the list, below the separator and
         * on the destructive role, because the group above only ever changes
         * where a thread sits. Delete is here rather than beside Rename for
         * that reason and no other: it belongs to every row, not only to
         * whichever thread happens to be open in the pane.
         */}
        {hasDestructiveActions ? (
          <>
            {hasOpenActions || hasOrganizeActions ? (
              <DropdownMenuSeparator />
            ) : null}
            {onArchiveThread ? (
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onArchiveThread(item.id)}
              >
                <Icon icon={Trash2} size="sm" />
                Archive
              </DropdownMenuItem>
            ) : null}
            {onDeleteThread ? (
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onDeleteThread(item.id)}
              >
                <Icon icon={Trash2} size="sm" />
                Delete
              </DropdownMenuItem>
            ) : null}
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Leading Slack origin. 16px well, so the control rung stays 32px. */
function ThreadRailOriginTag() {
  return (
    <Box
      render={<span />}
      data-slot="thread-rail-origin-tag"
      data-provider="slack"
      className="inline-flex size-4 shrink-0 items-center justify-center rounded-compact border border-line bg-muted"
    >
      <ProviderLogo provider="slack" title="Slack" className="size-3" />
    </Box>
  );
}

/** Running takes precedence; otherwise the blue dot means unread. */
function ThreadStatusMark({ status, unread }: { status: ThreadRailItem["statusName"]; unread?: boolean }) {
  if (status === "working") {
    return <Spinner size="sm" label="Running" className="shrink-0 text-ink-subtle" />;
  }
  if (unread) {
    return <Box role="img" aria-label="Unread" className="size-1.5 shrink-0 rounded-full bg-primary" />;
  }
  if (status === "attention") {
    return <Icon icon={AlertTriangle} size="sm" label="Needs attention" className="shrink-0 text-attention" />;
  }
  return null;
}

/** 48px icon-rail thread: a scannable dot, title on the Tooltip. */
function ThreadDotRow({
  item,
  onSelectThread,
  onPrefetchThread,
}: {
  item: ThreadRailItem;
  onSelectThread?: (id: string) => void;
  onPrefetchThread?: (id: string) => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            data-testid={`thread-rail-row-${item.id}`}
            data-slot="thread-rail-dot"
            data-selected={item.selected || undefined}
            aria-label={item.title}
            aria-current={item.selected ? "true" : undefined}
            onClick={() => onSelectThread?.(item.id)}
            onPointerEnter={() => onPrefetchThread?.(item.id)}
            onFocus={() => onPrefetchThread?.(item.id)}
            className={cn(
              "w-full",
              item.selected ? "bg-selected hover:bg-selected" : undefined
            )}
          >
            {item.unread || item.statusName === "working" || item.statusName === "attention" ? (
              <ThreadStatusMark status={item.statusName} unread={item.unread} />
            ) : (
              <Box aria-hidden className="size-1.5 rounded-full bg-ink-subtle" />
            )}
          </Button>
        }
      />
      <TooltipContent side="right">{item.title}</TooltipContent>
    </Tooltip>
  );
}

function ThreadRow({
  item,
  drag,
  rich,
  projects,
  onSelectThread,
  onArchiveThread,
  onDeleteThread,
  onMoveThread,
  onPinThread,
  onMarkThreadRead,
  onMarkThreadUnread,
  onPrefetchThread,
  onRenameThread,
}: {
  item: ThreadRailItem;
  drag?: ThreadRailDrag;
  rich: boolean;
  projects?: readonly ThreadRailProject[];
  onSelectThread?: (id: string) => void;
  onArchiveThread?: (id: string) => void;
  onDeleteThread?: (id: string) => void;
  onMoveThread?: (id: string, projectId: string | null) => void;
  onPinThread?: (id: string, pinned: boolean) => void;
  onMarkThreadRead?: (id: string) => void;
  onMarkThreadUnread?: (id: string) => void;
  onPrefetchThread?: (id: string) => void;
  onRenameThread?: (id: string) => void;
}) {
  const showSlackOrigin = item.provider === "slack";

  const rowBody = (
    <Inline
      gap="sm"
      align="center"
      className="min-w-0 flex-1"
    >
      {item.pinned ? <Icon icon={PushPin} size="sm" label="Pinned" className="text-ink-subtle" /> : null}
      {showSlackOrigin ? <ThreadRailOriginTag /> : null}
      <Stack gap="none" className="min-w-0 flex-1">
        <Box
          render={<span />}
          title={item.title}
          className="truncate text-label font-medium text-ink"
        >
          {item.title}
        </Box>
        {rich && item.subtitle ? (
          <Box
            render={<span />}
            className="truncate text-meta text-ink-subtle"
          >
            {item.subtitle}
          </Box>
        ) : null}
      </Stack>
      <ThreadStatusMark status={item.statusName} unread={item.unread} />
    </Inline>
  );

  return (
    <SidebarTreeRow
      data-testid={`thread-rail-row-${item.id}`}
      onPointerEnter={() => onPrefetchThread?.(item.id)}
      onFocus={() => onPrefetchThread?.(item.id)}
      selected={item.selected}
      multiline={rich && Boolean(item.subtitle)}
      className={cn("group/thread-row", drag?.item?.id === item.id && "opacity-50")}
    >
      <Box
        role="button"
        tabIndex={0}
        draggable={drag !== undefined}
        onDragStart={(event) => {
          if (!drag) return;
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("application/x-gtm-thread", item.id);
          drag.start(item);
        }}
        onDragEnd={() => drag?.end()}
      onClick={() => onSelectThread?.(item.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelectThread?.(item.id);
        }
      }}
        className="flex w-full min-w-0 cursor-pointer rounded-compact pr-7 outline-none focus-visible:ring-2 focus-visible:ring-primary"
      >
        {rowBody}
      </Box>
      <Box
        data-slot="thread-rail-row-actions"
        className="absolute inset-y-0 right-1 flex w-7 items-center justify-center"
      >
        <AgentThreadMenu
          item={item}
          projects={projects}
          onArchiveThread={onArchiveThread}
          onDeleteThread={onDeleteThread}
          onMoveThread={onMoveThread}
          onPinThread={onPinThread}
              onMarkThreadRead={onMarkThreadRead}
              onMarkThreadUnread={onMarkThreadUnread}
          onRenameThread={onRenameThread}
        />
      </Box>
    </SidebarTreeRow>
  );
}

const GROUPING_COPY: Record<ThreadRailGroupBy, string> = {
  project: "Project",
  origin: "Origin",
};

const ORDERING_COPY: Record<ThreadRailSort, string> = {
  recent: "Recency",
  title: "Title",
  status: "Status",
};

const SHOW_COPY: Record<ThreadRailFilter, string> = {
  all: "All",
  unread: "Unread",
  attention: "Needs attention",
};

/** Current value on a Grouping / Ordering / Show row, ahead of the submenu chevron. */
function MenuCurrentValue({ children }: { children: string }) {
  return (
    <Box render={<span />} className="text-meta text-ink-subtle">
      {children}
    </Box>
  );
}

const FILTER_MENU_ROW_CLASS = "min-h-control-sm gap-2 px-2";

function FilterOption({
  icon,
  children,
}: {
  icon: Glyph;
  children: string;
}) {
  return (
    <>
      <Icon icon={icon} size="sm" className="text-ink-subtle" />
      {children}
    </>
  );
}

/**
 * Filter menu: Grouping, Ordering, Show as submenus, not three peer icons.
 * Lives on the Conversations group header. Icons belong on the options
 * (Status, Project, Recency, …), not on the parent rows.
 */
function DisplayMenu({
  filter,
  groupBy = "project",
  onCollapseAll,
  onFilterChange,
  onGroupByChange,
  onSortChange,
  sort,
}: {
  filter: ThreadRailFilter;
  groupBy?: ThreadRailGroupBy;
  onCollapseAll?: () => void;
  onFilterChange?: (next: ThreadRailFilter) => void;
  onGroupByChange?: (next: ThreadRailGroupBy) => void;
  onSortChange?: (next: ThreadRailSort) => void;
  sort: ThreadRailSort;
}) {
  const show =
    onFilterChange !== undefined ||
    onGroupByChange !== undefined ||
    onSortChange !== undefined ||
    onCollapseAll !== undefined;

  if (!show) {
    return null;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Filter conversations"
          />
        }
      >
        <Icon icon={Filter} size="sm" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="right"
        align="start"
        sideOffset={6}
        className="min-w-56 gap-0.5"
      >
        {onGroupByChange ? (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className={FILTER_MENU_ROW_CLASS}>
              <Box render={<span />} className="flex-1">
                Grouping
              </Box>
              <MenuCurrentValue>{GROUPING_COPY[groupBy]}</MenuCurrentValue>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="min-w-44">
              <DropdownMenuRadioGroup
                value={groupBy}
                onValueChange={(next) => {
                  if (
                    next === "project" ||
                    next === "origin"
                  ) {
                    onGroupByChange(next);
                  }
                }}
              >
                <DropdownMenuRadioItem
                  value="project"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={Folder}>Project</FilterOption>
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem
                  value="origin"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={Globe}>Origin</FilterOption>
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ) : null}

        {onSortChange ? (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className={FILTER_MENU_ROW_CLASS}>
              <Box render={<span />} className="flex-1">
                Ordering
              </Box>
              <MenuCurrentValue>{ORDERING_COPY[sort]}</MenuCurrentValue>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="min-w-44">
              <DropdownMenuRadioGroup
                value={sort}
                onValueChange={(next) => {
                  if (
                    next === "recent" ||
                    next === "title" ||
                    next === "status"
                  ) {
                    onSortChange(next);
                  }
                }}
              >
                <DropdownMenuRadioItem
                  value="recent"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={Clock}>Recency</FilterOption>
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem
                  value="title"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={FileText}>Title</FilterOption>
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem
                  value="status"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={Layers}>Status</FilterOption>
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ) : null}

        {onFilterChange ? (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className={FILTER_MENU_ROW_CLASS}>
              <Box render={<span />} className="flex-1">
                Show
              </Box>
              <MenuCurrentValue>{SHOW_COPY[filter]}</MenuCurrentValue>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="min-w-44">
              <DropdownMenuRadioGroup
                value={filter}
                onValueChange={(next) => {
                  if (
                    next === "all" ||
                    next === "unread" ||
                    next === "attention"
                  ) {
                    onFilterChange(next);
                  }
                }}
              >
                <DropdownMenuRadioItem
                  value="all"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={List}>All</FilterOption>
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem
                  value="unread"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={Circle}>Unread</FilterOption>
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem
                  value="attention"
                  className="min-h-control-sm gap-2"
                >
                  <FilterOption icon={AlertTriangle}>Needs attention</FilterOption>
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ) : null}

        {onCollapseAll ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className={FILTER_MENU_ROW_CLASS}
              onClick={onCollapseAll}
            >
              Collapse all
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ThreadRailLoading({ compact = false }: { compact?: boolean }) {
  return (
    <Stack role="status" aria-label="Loading conversations" gap="none" className="px-2">
      {["w-3/4", "w-1/2", "w-2/3"].map((width) => (
        <Inline key={width} align="center" className="h-control">
          <Skeleton className={compact ? "size-4" : cn("h-3", width)} />
        </Inline>
      ))}
    </Stack>
  );
}

function ThreadRailGroupBlock({
  group,
  loading,
  drag,
  open,
  onOpenChange,
  projects,
  trailing,
  onSelectThread,
  onArchiveThread,
  onDeleteThread,
  onMoveThread,
  onPinThread,
  onMarkThreadRead,
  onMarkThreadUnread,
  onPrefetchThread,
  onRenameThread,
  onPinProject,
  collapsedGroupIds,
  onGroupOpenChange,
}: {
  group: ThreadRailGroup;
  loading: boolean;
  drag?: ThreadRailDrag;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projects?: readonly ThreadRailProject[];
  trailing?: ReactNode;
  onSelectThread?: (id: string) => void;
  onArchiveThread?: (id: string) => void;
  onDeleteThread?: (id: string) => void;
  onMoveThread?: (id: string, projectId: string | null) => void;
  onPinThread?: (id: string, pinned: boolean) => void;
  onMarkThreadRead?: (id: string) => void;
  onMarkThreadUnread?: (id: string) => void;
  onPrefetchThread?: (id: string) => void;
  onRenameThread?: (id: string) => void;
  onPinProject?: (id: string, pinned: boolean) => void;
  collapsedGroupIds?: readonly string[];
  onGroupOpenChange?: (id: string, open: boolean) => void;
}) {
  const target = threadDropTarget(group);
  const dropActive = target !== null && drag?.item != null;
  const acceptDrop = (event: DragEvent<HTMLElement>) => {
    if (!dropActive) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    drag?.over(group.id);
  };
  const rich =
    group.kind === "project" ||
    group.id === "pinned" ||
    group.id === "attention";

  return (
    <SidebarTreeGroup
      label={group.label}
      kind={group.kind === "project" ? "folder" : "section"}
      glyph={group.glyph}
      glyphClassName={group.glyphClassName}
      data-slot="thread-rail-group"
      data-drop-target={target === null ? undefined : group.id}
      data-drop-active={dropActive && drag?.overId === group.id ? "true" : undefined}
      onDragEnter={acceptDrop}
      onDragOver={acceptDrop}
      onDragLeave={(event) => {
        if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
        if (drag?.overId === group.id) drag.over(null);
      }}
      onDrop={(event) => {
        if (!dropActive || !target) return;
        event.preventDefault();
        event.stopPropagation();
        drag?.drop(group.id, target);
      }}
      open={open}
      onOpenChange={onOpenChange}
      className="data-[drop-active=true]:bg-selected data-[drop-active=true]:ring-1 data-[drop-active=true]:ring-line-strong"
      trailing={<>
        {group.kind === "project" && group.projectId && onPinProject ? (
          <Tooltip>
            <TooltipTrigger delay={100} render={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={
                  group.pinned
                    ? `Unpin project ${group.label}`
                    : `Pin project ${group.label}`
                }
                aria-pressed={group.pinned === true}
                className={cn(
                  "opacity-0 group-hover/sidebar-heading:opacity-100 group-focus-within/sidebar-heading:opacity-100",
                  group.pinned && "text-primary"
                )}
                onClick={() => {
                  if (group.projectId) onPinProject(group.projectId, !group.pinned);
                }}
              />
            }>
              <Icon icon={PushPin} size="md" />
            </TooltipTrigger>
            <TooltipContent side="right">
              {group.pinned ? "Unpin project" : "Pin project"}
            </TooltipContent>
          </Tooltip>
        ) : null}
        {trailing}
      </>}
    >
        <Stack gap="none">
          {group.projectGroups?.map((project) => (
            <ThreadRailGroupBlock
              key={project.id}
              group={project}
              loading={loading}
              drag={drag}
              open={!collapsedGroupIds?.includes(project.id)}
              onOpenChange={(open) => onGroupOpenChange?.(project.id, open)}
              collapsedGroupIds={collapsedGroupIds}
              onGroupOpenChange={onGroupOpenChange}
              projects={projects}
              onSelectThread={onSelectThread}
              onArchiveThread={onArchiveThread}
              onDeleteThread={onDeleteThread}
              onMoveThread={onMoveThread}
              onPinThread={onPinThread}
              onMarkThreadRead={onMarkThreadRead}
              onMarkThreadUnread={onMarkThreadUnread}
              onPrefetchThread={onPrefetchThread}
              onRenameThread={onRenameThread}
              onPinProject={onPinProject}
            />
          ))}
          {group.items.map((item) => (
            <ThreadRow
              key={item.id}
              item={item}
              drag={drag}
              rich={rich || item.attention === true}
              projects={projects}
              onSelectThread={onSelectThread}
              onArchiveThread={onArchiveThread}
              onDeleteThread={onDeleteThread}
              onMoveThread={onMoveThread}
              onPinThread={onPinThread}
              onMarkThreadRead={onMarkThreadRead}
              onMarkThreadUnread={onMarkThreadUnread}
              onPrefetchThread={onPrefetchThread}
              onRenameThread={onRenameThread}
            />
          ))}
          {loading && group.items.length === 0 && !group.projectGroups?.length ? (
            <ThreadRailLoading />
          ) : null}
          {!loading && group.kind === "project" && group.items.length === 0 ? (
            <Box render={<p />} className="px-2 py-1 text-meta text-ink-subtle">
              No conversations in this view
            </Box>
          ) : null}
        </Stack>
    </SidebarTreeGroup>
  );
}

/** The end-of-list affordance, both observed for scroll loading and operable directly. */
function ThreadLoadMoreControl({
  automatic,
  compact,
  loading,
  onLoadMore,
}: {
  automatic: boolean;
  compact: boolean;
  loading: boolean;
  onLoadMore: () => void;
}) {
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const onLoadMoreRef = useRef(onLoadMore);

  useEffect(() => {
    onLoadMoreRef.current = onLoadMore;
  }, [onLoadMore]);

  useEffect(() => {
    if (loading || !automatic) return;
    const node = nodeRef.current;
    if (node === null || typeof IntersectionObserver === "undefined") return;
    const root = node.closest("[data-slot=scroll-area-viewport]");
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) onLoadMoreRef.current();
      },
      {
        root: root instanceof Element ? root : null,
        rootMargin: "160px",
      }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [automatic, loading]);

  return (
    <Stack
      data-slot="thread-rail-load-more"
      gap="xs"
      align="center"
      className="py-1"
    >
      <Box
        render={<div ref={nodeRef} />}
        data-slot="thread-rail-load-sentinel"
        aria-hidden
        className="h-px w-full"
      />
      <Button
        type="button"
        variant="ghost"
        size={compact ? "icon-sm" : "compact"}
        aria-label="Load older conversations"
        aria-busy={loading}
        disabled={loading}
        onClick={onLoadMore}
      >
        {compact ? (
          <Icon icon={ChevronDown} size="sm" />
        ) : loading ? (
          "Loading older conversations"
        ) : (
          "Load older conversations"
        )}
      </Button>
    </Stack>
  );
}

/*
 * MEMOIZED, ON MEASURED EVIDENCE. The rail's host re-renders on every stream
 * frame and on every minute of the clock, and the rail is the most expensive
 * subtree on that surface: the PERF-0 baseline puts the rail's own search
 * interaction at a 120 to 128ms median under 4x throttle against a 32ms
 * composer keystroke, the only scenario anywhere near the 150ms lab budget
 * (`tests-perf/BASELINE.md`). React Compiler is off in this app, so nothing
 * else contains that work.
 *
 * The memo only pays if the host holds its props still. `groups` moves when the
 * clock relabels the rows, which is the rail's own reason to redraw; every
 * other prop (`leading`, `features`, and the callbacks) has to be stable at the
 * caller, or this boundary is a comparison the surface pays for and never
 * passes. `AgentSurface` holds all of them.
 */
const AgentThreadRail = memo(function AgentThreadRail({
  autoLoadMore = true,
  className,
  collapsedGroupIds,
  compact = false,
  features,
  filter = "all",
  footer,
  groupBy = "project",
  groups,
  hasMore = false,
  leading,
  loading = false,
  loadingMore = false,
  onArchiveThread,
  onCollapsedGroupIdsChange,
  onDeleteThread,
  onFilterChange,
  onGroupByChange,
  onLoadMore,
  onMoveThread,
  onDropThread,
  onPinProject,
  onPinThread,
  onMarkThreadRead,
  onMarkThreadUnread,
  onPrefetchThread,
  onRenameThread,
  onSearchChange,
  onSelectThread,
  onSortChange,
  projects,
  search = "",
  sort = "recent",
}: AgentThreadRailProps) {
  const [draggedThread, setDraggedThread] = useState<ThreadRailItem | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [uncontrolledCollapsed, setUncontrolledCollapsed] = useState<string[]>(
    []
  );

  const collapsed = collapsedGroupIds ?? uncontrolledCollapsed;
  const setCollapsed = (ids: readonly string[]) => {
    if (onCollapsedGroupIdsChange) {
      onCollapsedGroupIdsChange(ids);
      return;
    }
    setUncontrolledCollapsed([...ids]);
  };

  const isGroupOpen = (id: string) => !collapsed.includes(id);

  const setGroupOpen = (id: string, open: boolean) => {
    if (open) {
      setCollapsed(collapsed.filter((entry) => entry !== id));
      return;
    }
    if (collapsed.includes(id)) {
      return;
    }
    setCollapsed([...collapsed, id]);
  };

  const endDrag = () => {
    setDraggedThread(null);
    setDragOverId(null);
  };
  const drag: ThreadRailDrag | undefined = onDropThread ? {
    item: draggedThread,
    overId: dragOverId,
    start: setDraggedThread,
    end: endDrag,
    over: setDragOverId,
    drop: (groupId, target) => {
      // Only a row dragged from this rail may name the mutation's thread.
      if (!draggedThread) return;
      const alreadyThere = target.kind === "pinned"
        ? draggedThread.pinned === true
        : !draggedThread.pinned && (draggedThread.projectId ?? null) === target.projectId;
      if (!alreadyThere) onDropThread(draggedThread.id, target);
      setGroupOpen(groupId, true);
      endDrag();
    },
  } : undefined;
  const visibleGroups = [...groups];
  if (draggedThread) {
    if (!groups.some((group) => group.id === "pinned")) {
      visibleGroups.unshift({ id: "pinned", label: "Pinned", count: 0, items: [] });
    }
    if (!groups.some((group) => group.id === "no-project" || group.id === "threads")) {
      visibleGroups.push({ id: "no-project", label: "No project", count: 0, items: [] });
    }
  }

  const compactThreads = compact
    ? groups.flatMap((group) => [
        ...group.items,
        ...(group.projectGroups ?? []).flatMap((project) => project.items),
      ])
    : null;

  const displayHostId =
    groups.find((group) => group.id === "recent")?.id ?? groups[0]?.id;
  const displayMenuLive =
    onFilterChange !== undefined ||
    onGroupByChange !== undefined ||
    onSortChange !== undefined;

  return (
    <Stack
      data-slot="agent-thread-rail"
      data-testid="agent-thread-rail"
      data-compact={compact || undefined}
      gap="none"
      className={cn("h-full min-h-0 w-full", className)}
    >
      {/*
       * Equal `sm` pad around PostureBar (same recipe as the product rail).
       * Features + list sit in a flush-top stack so New thread is one
       * `sm` below Agent — matching the air under the brand.
       */}
      {leading ? (
        <Box padding="sm" className="shrink-0">
          {leading}
        </Box>
      ) : null}

      <Stack gap="sm" className="min-h-0 flex-1 px-2 pb-2">
        {features && features.length > 0 ? (
          <SidebarNav label="Agent features" flush>
            {features.map((feature) => (
              <SidebarNavItem
                key={feature.id}
                icon={feature.icon}
                label={feature.label}
                count={compact ? undefined : feature.count}
                selected={feature.selected}
                href={feature.href}
                onSelect={feature.onSelect}
                compact={compact}
              />
            ))}
          </SidebarNav>
        ) : null}

        {compact ? null : onSearchChange ? (
          <SearchInput
            label="Search conversations"
            maxLength={200}
            value={search}
            onValueChange={onSearchChange}
            placeholder="Search"
          />
        ) : null}

        <ScrollArea overflow="vertical" className="-mr-2 min-h-0 flex-1">
          <Stack gap="none" className="pr-3" aria-busy={loading || undefined}>
            {compact && compactThreads ? (
              <Stack
                data-slot="thread-rail-dots"
                gap="xs"
                align="stretch"
                className="pb-1"
              >
                {loading && compactThreads.length === 0 ? <ThreadRailLoading compact /> : null}
                {compactThreads.map((item) => (
                  <ThreadDotRow
                    key={item.id}
                    item={item}
                    onSelectThread={onSelectThread}
                    onPrefetchThread={onPrefetchThread}
                  />
                ))}
              </Stack>
            ) : (
              <Stack gap="sm" className="pt-2 pb-1">
                {groups.length === 0 ? (
                  <>
                    <Inline justify="between">
                      <Box render={<span />} className="text-meta text-ink-subtle">Threads</Box>
                      <DisplayMenu filter={filter} groupBy={groupBy} sort={sort}
                        onFilterChange={onFilterChange} onGroupByChange={onGroupByChange} onSortChange={onSortChange} />
                    </Inline>
                    {loading ? <ThreadRailLoading /> : (
                      <Box render={<p />} className="px-2 text-meta text-ink-subtle">
                        {search ? (hasMore ? "No matches in these conversations. Load older conversations to keep searching." : "No matching conversations") : "No threads yet"}
                      </Box>
                    )}
                  </>
                ) : null}
                {visibleGroups.map((group) => (
                  <ThreadRailGroupBlock
                    key={group.id}
                    group={group}
                    loading={loading}
                    drag={drag}
                    open={isGroupOpen(group.id)}
                    onOpenChange={(open) => setGroupOpen(group.id, open)}
                    collapsedGroupIds={collapsed}
                    onGroupOpenChange={setGroupOpen}
                    projects={projects}
                    trailing={
                      group.id !== displayHostId || !displayMenuLive
                        ? undefined
                        : (
                            <DisplayMenu
                              filter={filter}
                              groupBy={groupBy}
                              sort={sort}
                              onFilterChange={onFilterChange}
                              onGroupByChange={onGroupByChange}
                              onSortChange={onSortChange}
                              onCollapseAll={() =>
                                setCollapsed(groups.flatMap((entry) => [entry.id, ...(entry.projectGroups ?? []).map((project) => project.id)]))
                              }
                            />
                          )
                    }
                    onSelectThread={onSelectThread}
                    onArchiveThread={onArchiveThread}
                    onDeleteThread={onDeleteThread}
                    onMoveThread={onMoveThread}
                    onPinThread={onPinThread}
              onMarkThreadRead={onMarkThreadRead}
              onMarkThreadUnread={onMarkThreadUnread}
                    onPrefetchThread={onPrefetchThread}
                    onRenameThread={onRenameThread}
                    onPinProject={onPinProject}
                  />
                ))}
                {!loading && search && groups.length > 0 && groups.every((group) => group.items.length === 0 && (group.projectGroups ?? []).every((project) => project.items.length === 0)) ? (
                  <Box render={<p />} className="px-2 text-meta text-ink-subtle">
                    {hasMore ? "No matches in these conversations. Load older conversations to keep searching." : "No matching conversations"}
                  </Box>
                ) : null}
              </Stack>
            )}
            {hasMore && onLoadMore !== undefined ? (
              <ThreadLoadMoreControl
                automatic={autoLoadMore}
                compact={compact}
                loading={loadingMore}
                onLoadMore={onLoadMore}
              />
            ) : null}
          </Stack>
        </ScrollArea>

        {footer ? (
          <Box className="-mx-2 border-t border-line px-2 py-2">{footer}</Box>
        ) : null}
      </Stack>
    </Stack>
  );
});

export { AgentThreadRail, AgentThreadMenu, AGENT_THREAD_RAIL_RULES };
export type {
  AgentThreadRailProps,
  ThreadRailDropTarget,
  ThreadRailFeature,
  ThreadRailFilter,
  ThreadRailGroup,
  ThreadRailGroupBy,
  ThreadRailItem,
  ThreadRailProject,
  ThreadRailSort,
};
