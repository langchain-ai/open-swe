"use client";

/*
 * Rules for ChangeFeed.
 *
 * Home quieter band (CORE 01/13). Flat hairline rows — observational, no
 * action lane, no Frame card. Containment ledger: the decision queue is a
 * panel; this feed is a window onto something continuous.
 *
 * One list carries both halves of what-changed: agent receipts and world
 * changes, interleaved, compressed behind a clipped continuation.
 */

import { useState, type KeyboardEvent, type ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import { CheckCircle, ChevronDown } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { ScrollArea } from "../ui/scroll-area";

const CHANGE_FEED_RULES: readonly string[] = [
  "ChangeFeed is the quieter Home band under the DecisionRow queue. It is observational — no Approve CTA, no action lane. Home is a router, so a row that can name a destination opens that surface on the whole row, the same verb Coming up uses.",
  'ONE list carries both halves (platform decision 1.5). `kind: "RECEIPT"` is what the agent did since you last looked; `kind: "WORLD"` is what happened out there — replies, meetings moved, CRM writes. They interleave in time order. Never split them into two bands, two tabs, or a filter: the receipts half is what makes an empty decision queue trustworthy, and it only does that job standing beside the world half.',
  "A receipt renders in the compact inline Receipt form (`surface-decisions` §6): the same hairline rung, prefixed by the receipt marker so agent-authored facts stay distinguishable from system facts. The Frame-shelled `Receipt` is the inspectable detail one hop away — it never appears in this band.",
  "Flat, not a Frame. `PAGE_SECTION_RULES` settles it: the decision queue is a panel; 'Changes to know' is a window onto something continuous. A bordered card with matching left/right chrome pad is a containment lie.",
  "Each item: relative time · marker · object · one-line change on the 44px record rung, hairline between rows, `px-3` once — same inset as DecisionRow. The marker lane is reserved on every row and filled only on receipts, so both halves share one column grid. Compose DiffRow when a field mutation needs structure.",
  "'Compressed' is five rows on the page and View more. View more expands the same panel in place, capped, and the list scrolls inside. The trigger stays outside the scroll. It is not a popover, and it is not a pager. The continuation is band chrome on the quieter 40px rung, not the action lane the first rule bans.",
  "`GET /v1/activity` is the briefing this band renders. `/v1/changes` is the invalidation log, ids only. Never join the queue, upcoming, drafts, threads, or alerts in the page to decorate a row. If the band needs a title or href the briefing does not serve, the endpoint moves. A row opens the owning surface when the briefing names an href.",
];

/** Which half of what-changed an item belongs to (platform decision 1.5). */
type ChangeFeedKind = "RECEIPT" | "WORLD";

/** Rows kept on the page; View more reveals the rest in the same list. */
const DEFAULT_COLLAPSED_COUNT = 5;

const CONTINUATION_TRIGGER_CLASS =
  "h-row-data w-full cursor-pointer px-3 text-meta text-ink-subtle outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary";

/** Taller than the five-row rest, then the expanded list scrolls inside. */
const EXPANDED_LIST_VIEWPORT_CLASS = "max-h-96";

interface ChangeFeedItem {
  id: string;
  /** RECEIPT is the agent's own work; WORLD is what happened to us. */
  kind: ChangeFeedKind;
  time: string;
  object: string;
  change: ReactNode;
  /** Whole-row open. Omit when the id is not enough to route. */
  onOpen?: () => void;
}

function ChangeFeedRow({ change, id, kind, object, onOpen, time }: ChangeFeedItem) {
  const activate = () => onOpen?.();
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (onOpen === undefined) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    activate();
  };

  return (
    <Inline
      data-slot="change-feed-row"
      data-testid={`change-feed-item-${id}`}
      data-kind={kind}
      tabIndex={onOpen === undefined ? undefined : 0}
      onClick={onOpen === undefined ? undefined : activate}
      onKeyDown={onOpen === undefined ? undefined : onKeyDown}
      gap="sm"
      align="center"
      className={cn(
        "h-row-record w-full border-b border-line px-3 last:border-b-0",
        onOpen === undefined
          ? undefined
          : "cursor-pointer outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary"
      )}
    >
      <Box
        render={<span />}
        className="w-10 shrink-0 font-mono text-meta text-ink-subtle tabular-nums"
      >
        {time}
      </Box>
      <Inline align="center" justify="center" className="w-4 shrink-0">
        {kind === "RECEIPT" ? (
          <Icon
            icon={CheckCircle}
            size="sm"
            label="Agent receipt"
            className="text-ink-subtle"
          />
        ) : null}
      </Inline>
      <Box
        render={<span />}
        className="w-36 shrink-0 truncate text-label font-medium text-ink"
      >
        {object}
      </Box>
      <Box
        render={<span />}
        className="min-w-0 flex-1 truncate text-label text-ink-subtle"
      >
        {change}
      </Box>
    </Inline>
  );
}

interface ChangeFeedProps {
  items: readonly ChangeFeedItem[];
  /** Rows rendered on the page; View more reveals the rest in the same list. */
  collapsedCount?: number;
  className?: string;
}

interface ListContinuationProps {
  expanded: boolean;
  onToggle: () => void;
  testId: string;
}

/**
 * Band chrome under a clipped Home list. View more expands the same panel.
 * Shared by ChangeFeed, the decision queue, and Coming up's list view.
 */
function ListContinuation({
  expanded,
  onToggle,
  testId,
}: ListContinuationProps) {
  return (
    <Inline
      render={<button type="button" />}
      data-testid={testId}
      aria-expanded={expanded}
      gap="xs"
      align="center"
      className={CONTINUATION_TRIGGER_CLASS}
      onClick={onToggle}
    >
      <Box
        aria-hidden
        className={cn(
          "transition-transform duration-fast ease-out-quint motion-reduce:transition-none",
          expanded && "rotate-180"
        )}
      >
        <Icon icon={ChevronDown} size="sm" />
      </Box>
      {expanded ? "View less" : "View more"}
    </Inline>
  );
}

/** Caps an expanded Home list and scrolls inside it. Collapsed lists stay uncapped. */
function ListViewport({
  constrained,
  children,
}: {
  constrained: boolean;
  children: ReactNode;
}) {
  return (
    <ScrollArea
      overflow="vertical"
      viewportClassName={constrained ? EXPANDED_LIST_VIEWPORT_CLASS : undefined}
    >
      {children}
    </ScrollArea>
  );
}

function clipList<T>(
  items: readonly T[],
  collapsedCount: number
): {
  clipped: boolean;
  remaining: number;
  rest: readonly T[];
  visible: readonly T[];
} {
  const clipped = items.length > collapsedCount;
  if (!clipped) {
    return { clipped: false, remaining: 0, rest: [], visible: items };
  }
  return {
    clipped: true,
    remaining: items.length - collapsedCount,
    rest: items.slice(collapsedCount),
    visible: items.slice(0, collapsedCount),
  };
}

function ChangeFeed({
  className,
  collapsedCount = DEFAULT_COLLAPSED_COUNT,
  items,
}: ChangeFeedProps) {
  const { clipped, visible } = clipList(items, collapsedCount);
  const [expanded, setExpanded] = useState(false);
  const shown = clipped && !expanded ? visible : items;

  return (
    <Stack
      data-slot="change-feed"
      data-testid="change-feed"
      gap="none"
      className={cn("w-full", className)}
    >
      <ListViewport constrained={clipped && expanded}>
        <Stack gap="none" className="w-full overflow-hidden">
          {shown.map((item) => (
            <ChangeFeedRow key={item.id} {...item} />
          ))}
        </Stack>
      </ListViewport>
      {clipped ? (
        <ListContinuation
          testId="change-feed-continuation"
          expanded={expanded}
          onToggle={() => setExpanded((open) => !open)}
        />
      ) : null}
    </Stack>
  );
}

export {
  ChangeFeed,
  CHANGE_FEED_RULES,
  DEFAULT_COLLAPSED_COUNT,
  ListContinuation,
  ListViewport,
  clipList,
};
export type { ChangeFeedItem, ChangeFeedKind, ChangeFeedProps };
