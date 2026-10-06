"use client";

/*
 * Rules for SplitView.
 *
 * The rules themselves are `SPLIT_VIEW_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the split a
 * builder reads in the gallery and the split this file lays out are the same
 * strings.
 *
 * The decision is CORE 07's dock panel (Paper file
 * 01KYQDVRVBZ6KYPDP179BS2M3G, node QUM-0): panes are not left and right, they
 * are roles. Two compositions are legal:
 *
 *   work + reference — decision left (grows), context right (yields)
 *   list + work      — triage list left (inner sidebar), decision right
 *                      (grows). Inbox. Status tabs / search / grouping live IN
 *                      the list pane, never as a full-width strip above the
 *                      split — that was the chrome ownership bug.
 *
 * What the code enforces: the roles, the yield direction, list sticky chrome
 * owning its own scroll, and the half of the state-survival contract that is
 * structural. Both panes are mounted in the same tree positions in every
 * posture, neither is keyed, and posture / collapse / resize change geometry
 * classes or width only, so React has no reason to unmount anything a rep was
 * in the middle of.
 *
 * Pane widths change immediately. Collapse, yield and pointer resize never
 * interpolate layout or delay keyboard navigation.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import type { Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { ScrollArea } from "../ui/scroll-area";

const SPLIT_VIEW_RULES: readonly string[] = [
  "Panes have roles, not sides. One is the work pane, where the decision happens; the other is either the reference pane (facts the decision is made against) or the list pane (the triage sidebar that picks which decision is on screen). Naming them is the whole pattern: 'left' and 'right' cannot tell you which one is allowed to shrink.",
  "Two compositions, never a mash-up: `work + reference` (decision grows, context yields) or `list + work` (inner sidebar, decision grows). Inbox is list + work. Do not put the conversation list in `workPane` and the thread in a narrow `referencePane` — that inverts roles and cramps ApprovalArtifact.",
  "List chrome belongs to the list pane. Title, search, status Tabs, and grouping mount inside `listPane` via `ListSidebarChrome` — never as a full-width strip above the SplitView. A filter that spans the thread is claiming territory it does not own. Title-row Filters and Group by are icon-only (`trigger=\"icon\"`, `icon-sm` ghost). The noun lives on `aria-label`. A labeled outline Group by belongs on the FilterableTable toolbar, not beside a list title.",
  "List sidebar titles use `ListSidebarTitle` — optional leading glyph at `Icon` `lg` (20px) plus `text-title font-semibold`, on the `h-control` rung. That rung is not decoration: `AppRailBrand` is `h-control`, and the list title sits across one vertical seam from it, so they are the same construction rather than two numbers that happen to add up.",
  "List chrome top air is `pt-2`, which is the rail's own `pt-2` — the same air above the product wordmark, so the two mastheads share a baseline. Never re-derive it by adding a child's padding to the rail's; that arithmetic drifts the moment either row changes rung, and it did. Bottom air is `pb-2` — the same pad the alert inspector header spends above its hairline. Never override it at a call site. The stack gap is `sm` for title, search, and tabs; do not invent a second gap between search and tabs. Host-scroll shell cards stay flush so the list pane and resize handle go edge-to-edge — never park AppShell card `pt-2` above a SplitView.",
  "One lane down the whole pane: `ListSidebarChrome` owns `px-3`, and rows own `px-3` themselves and sit flush in the scroll surface. Never wrap the rows in a padding container — a second, smaller inset puts the rows out of line with the title and search above them, and puts one list pane out of line with every other. States inside the scroll surface (error, empty) take `Box padding=\"md\"`, not the row lane.",
  "The list pane owns its scroll. Sticky chrome sits above a ScrollArea of rows; SplitView does not wrap `listPane` in a second ScrollArea (that would scroll the chrome away). The work pane defaults to the same pattern's ScrollArea. A table that must keep its toolbar put takes `workScroll=\"contained\"`: the pane fills the host and does not scroll, and FilterableTable's grid body is the scroll surface.",
  "The pane that holds reference yields; the pane where the decision happens keeps its width. When the agent docks at 420px the reference pane narrows (or, in list + work without a user width, the list narrows a step) and the work pane does not move. A surface where the conversation shrinks so a fact panel can stay wide has its roles backwards.",
  "A closed reference pane hides with CSS (`referenceHidden`): width 0, no seam, still mounted. Accounts closes the record panel and the table stays; Alerts does the same for the inspector. Do not swap the split for a single pane, and do not park an empty state in a closed rail.",
  "Inspection opens from an explicit row click and preserves the list or table's filters, scroll, selection, and draft state. Start with the reference pane closed when no standing reference is required; do not reserve an empty inspector to advertise that rows are clickable. A full route is for a durable workspace, not a read-only peek that makes the user rebuild list context on Back.",
  "A reference pane that holds a framed artifact (the Alerts inspector) takes `referenceSeam={false}`. The Frame is the outline; a pane hairline beside it is a second border. That inspector also takes `referenceScroll=\"contained\"` so identity and hops stay put while the body scrolls — the pattern's ScrollArea would otherwise roll the chrome away.",
  "Per-surface yield, settled: overview, the canvas re-centers and nothing is lost; inbox, the list sidebar steps down and the thread stays; accounts, the record panel closes and the table stays; campaign, the outcomes rail folds into the snapshot; data grids scroll horizontally with the first column sticky.",
  "Thread, draft, approval state, scroll position and composer text survive every posture change. A posture is a width, not a route: if toggling the dock loses a half-written reply, the bug is that something was unmounted, not that the user should have saved.",
  "That contract is why posture never swaps one component for another. Both panes stay mounted in the same tree positions in every posture, neither takes a key that varies, and a pane that must disappear hides with CSS rather than being conditionally rendered. Rendering `posture === 'docked' ? <Chips/> : <Rail/>` remounts the subtree and throws away exactly the state this rule protects. List collapse is the same law at a narrower width (48px icon rail) — the pane stays mounted.",
  "Anything a pane must not lose across a posture change lives in that pane's own component or above the SplitView, never in a wrapper the posture recreates.",
  "Two panes, never three. A third region on a surface is the agent dock, and the dock is the shell's, not this pattern's; a split view that grew a third pane is a surface that needed a route. `listPane` and `referencePane` are mutually exclusive.",
  "Panes divide the measure; rails sit outside it. AppShell's `contentWidth` caps the content region, and a SplitView mounted there splits what the cap leaves. The navigation rail and the agent dock are the shell's chrome, outside the measure. (`APP_SHELL_RULES`; `docs/plan/gtm-agent-product/width-padding-evidence.md` section 5.3 rules 2, 4 and 7.)",
  "AppShell that mounts this pattern MUST set `contentScroll=\"host\"` (focus already defaults). Shell ScrollArea is the root bug: it collapses `h-full`, so list + work pile at the top of an empty viewport. ESLint `gtm/design-law/app-shell-host-scroll` enforces it. Never paper over a missing host with page-local `h-screen` / sticky hacks.",
  "List sidebar defaults: 384 open / 320 docked / 48 collapsed. Inbox opts into ReUI app-shell-4's **440** open list (`listWidth={440}` as the initial width when uncontrolled; drag clamped **200–520**) plus `listCollapsed`. Reference context stays 300 / docked 200 (CORE 02 NKJ-0, CORE 07 QUO-0). A report artifact (Alerts, Scheduled) takes `referenceWidth=\"report\"`: 400 / docked 320 (CORE 08 RMO-0). `referenceResizable` puts a seam handle on the leading edge (clamped **280–640**); drag disables the width ease so the handle tracks 1:1.",
  "Opening a reference pane must not change AppShell `contentWidth`. The work column stays; the rail eases in. Switching reading→wide recenters the list the user was looking at.",
];

type SplitViewPosture = "default" | "docked";

type SplitViewReferenceWidth = "context" | "report";

/*
 * Context is 300px: the Account Context Rail on CORE 02 (NKJ-0). Docked, it
 * yields to 200px. Report is 400px: CORE 08's Alerts inspector (RMO-0). Docked,
 * that yields to 320px. A generic reference pane cannot collapse without
 * unmounting, which the state-survival rule above forbids, so it narrows
 * instead.
 */
const REFERENCE_WIDTH_CLASS: Record<
  SplitViewReferenceWidth,
  Record<SplitViewPosture, string>
> = {
  context: { default: "w-75", docked: "w-50" },
  report: { default: "w-100", docked: "w-80" },
};

const LIST_WIDTH_DEFAULT: Record<SplitViewPosture, number> = {
  default: 384,
  docked: 320,
};

/** Class path when the list does not opt into collapse / resize. */
const LIST_WIDTH_CLASS: Record<SplitViewPosture, string> = {
  default: "w-96",
  docked: "w-80",
};

const LIST_COLLAPSED_WIDTH = 48;
/** Floor for a triage list: title + filter still fit; drag can go slim. */
const LIST_WIDTH_MIN = 200;
const LIST_WIDTH_MAX = 520;

const REFERENCE_WIDTH_PX: Record<
  SplitViewReferenceWidth,
  Record<SplitViewPosture, number>
> = {
  context: { default: 300, docked: 200 },
  report: { default: 400, docked: 320 },
};

/** Floor for a report rail: labeled cards still fit; drag can go slim. */
const REFERENCE_WIDTH_MIN = 280;
const REFERENCE_WIDTH_MAX = 640;

const REFERENCE_PANE_CLASS = "relative shrink-0 motion-reduce:transition-none";


const REFERENCE_SEAM_CLASS = "border-l border-line";

/** Closed, not unmounted: the table keeps its width and the pane keeps its tree. */
const REFERENCE_HIDDEN_CLASS = "pointer-events-none w-0 overflow-hidden";

const LIST_PANE_CLASS =
  "relative shrink-0 border-r border-line motion-reduce:transition-none";

/*
 * Each scrolling pane scrolls itself through a ScrollArea rather than
 * `overflow-auto`. The scrollbar law (wiki 02) forbids a native always-on bar
 * on an app surface, and a split view is where it would hurt most: two
 * permanent gutters facing each other across the divider, both stealing width
 * from panes that are already negotiating for it. The overlay thumb costs no
 * width at all, which is also why the reference pane's yield stays a clean
 * 300 to 200 rather than 300 minus a bar.
 *
 * The list pane is the exception: it owns sticky chrome (search / tabs /
 * grouping) above its own ScrollArea of rows. Wrapping the whole list pane
 * would scroll that chrome away — the bug the Inbox full-width strip was
 * papering over.
 *
 * Where SplitView does wrap a ScrollArea, it sits inside the pane, not around
 * it, so the two `data-slot` nodes stay the same elements across a posture
 * change: the state-survival rule above is about what React remounts, and a
 * wrapper at the pane boundary is exactly the kind of thing that quietly
 * starts remounting subtrees.
 */
const PANE_SCROLL_CLASS = "min-h-0 flex-1";

const WORK_CONTAINED_CLASS = "min-h-0 min-w-0 overflow-hidden";

function SplitViewWorkPane({
  label,
  scroll = "pane",
  children,
}: {
  label: string;
  scroll?: "pane" | "contained";
  children: ReactNode;
}) {
  return (
    <Stack
      data-slot="split-view-work"
      data-scroll={scroll}
      render={<section aria-label={label} />}
      grow
      className={scroll === "contained" ? WORK_CONTAINED_CLASS : "min-w-0"}
    >
      {scroll === "contained" ? (
        children
      ) : (
        <ScrollArea recoveryKey={`work:${label}`} className={PANE_SCROLL_CLASS}>{children}</ScrollArea>
      )}
    </Stack>
  );
}

/**
 * Title for a `list + work` inner sidebar (Inbox, and any peer triage rail).
 * Optional leading icon at the `lg` rung + section title so the pane reads as
 * a named surface, not a row label.
 */
function ListSidebarTitle({
  children,
  className,
  icon,
}: {
  children: ReactNode;
  className?: string;
  /** Surface glyph — Inbox, etc. Drawn at `lg` (20px), the page-title peer. */
  icon?: Glyph;
}) {
  return (
    <Inline
      data-slot="list-sidebar-title"
      gap="sm"
      align="center"
      className={cn("h-control min-w-0 shrink-0", className)}
    >
      {icon ? (
        <Icon
          icon={icon}
          size="lg"
          className="shrink-0 text-ink"
        />
      ) : null}
      <Box
        render={<h2 />}
        className="min-w-0 truncate text-title font-semibold text-ink"
      >
        {children}
      </Box>
    </Inline>
  );
}

/**
 * Sticky chrome stack for a list sidebar — title, search, tabs.
 *
 * TOP AIR MATCHES THE RAIL'S, AND THE TITLE MATCHES THE MASTHEAD'S RUNG. The
 * list title and the product wordmark sit either side of one vertical seam, so
 * a reader sees them as one band or as a mistake. They line up because both are
 * the same construction and not because two numbers were added up: `pt-2` here
 * is the rail's own `pt-2`, and `ListSidebarTitle` takes `h-control` because
 * `AppRailBrand` is `h-control`. This used to be `pt-3`, reasoned as "rail
 * `pt-2` + brand `py-1`" against a lockup that renders as a 32px row rather
 * than a padded one, which left the two baselines 2px apart with nothing in
 * either file to say why. Bottom air is the same `pb-2` the inspector header
 * uses; the stack gap is `sm` for every chrome child (title, search, tabs).
 */
const LIST_SIDEBAR_CHROME_CLASS =
  "shrink-0 overflow-hidden border-b border-line px-3 pb-2 pt-2";

function ListSidebarChrome({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <Stack
      data-slot="list-sidebar-chrome"
      gap="sm"
      className={cn(LIST_SIDEBAR_CHROME_CLASS, className)}
    >
      {children}
    </Stack>
  );
}

type SplitViewBaseProps = {
  /** Where the decision happens. Keeps its width in every posture. */
  workPane: ReactNode;
  /** Names the work landmark, e.g. "Thread". */
  workLabel: string;
  /** `docked` is the 420px agent dock case: the yielding pane narrows. */
  posture?: SplitViewPosture;
  /**
   * `pane` (default): the work pane scrolls as a whole.
   * `contained`: the pane fills the host and does not scroll — the work
   * surface owns its own scroll (a filled FilterableTable, a transcript).
   */
  workScroll?: "pane" | "contained";
};

type SplitViewReferenceProps = SplitViewBaseProps & {
  /** What the decision is made against. This is the pane that yields. */
  referencePane: ReactNode;
  /** Names the reference landmark, e.g. "Account context". */
  referenceLabel: string;
  /**
   * Close the record panel. The pane stays mounted (state survival); width
   * and the seam go to zero so the work pane takes the measure.
   */
  referenceHidden?: boolean;
  /**
   * Draw the pane hairline. Default on. Off when the pane holds a Frame
   * (Alerts inspector): that artifact already owns the outline.
   */
  referenceSeam?: boolean;
  /**
   * How wide the open pane is. `context` is 300 (CORE 02). `report` is 400
   * (CORE 08 Alerts inspector).
   */
  referenceWidth?: SplitViewReferenceWidth;
  /**
   * `pane` (default): the reference rail scrolls as a whole.
   * `contained`: the pane fills and does not scroll — the inspector owns
   * identity, body scroll, and hops (Alerts).
   */
  referenceScroll?: "pane" | "contained";
  listPane?: never;
  listLabel?: never;
  listCollapsed?: never;
  onListCollapsedChange?: never;
  /** Drag the leading seam to resize the open rail (clamped 280–640). */
  referenceResizable?: boolean;
  listResizable?: never;
  listWidth?: never;
  onListWidthChange?: never;
};

type SplitViewListProps = SplitViewBaseProps & {
  /**
   * Triage sidebar (Inbox). Owns title / search / status Tabs / grouping
   * and its own row ScrollArea. Mutually exclusive with `referencePane`.
   */
  listPane: ReactNode;
  /** Names the list landmark, e.g. "Inbox conversations". */
  listLabel: string;
  referencePane?: never;
  referenceLabel?: never;
  referenceHidden?: never;
  referenceSeam?: never;
  referenceWidth?: never;
  referenceScroll?: never;
  referenceResizable?: never;
  /**
   * Collapse the list to the 48px icon rail. Pane stays mounted (state
   * survival); chrome inside decides what the rail shows.
   */
  listCollapsed?: boolean;
  onListCollapsedChange?: (collapsed: boolean) => void;
  /** Drag the seam to resize the open list width (clamped 200–520). */
  listResizable?: boolean;
  /**
   * Open width in px. With `onListWidthChange`, controlled. Without it, seeds
   * the uncontrolled width (Inbox's 440 default) so resize still works.
   */
  listWidth?: number;
  onListWidthChange?: (width: number) => void;
};

type SplitViewProps = SplitViewReferenceProps | SplitViewListProps;

function isSplitViewList(props: SplitViewProps): props is SplitViewListProps {
  return props.listPane !== undefined;
}

function clampListWidth(width: number): number {
  return Math.min(LIST_WIDTH_MAX, Math.max(LIST_WIDTH_MIN, Math.round(width)));
}

function clampReferenceWidth(width: number): number {
  return Math.min(
    REFERENCE_WIDTH_MAX,
    Math.max(REFERENCE_WIDTH_MIN, Math.round(width))
  );
}

function PaneResizeHandle({
  clamp,
  edge,
  width,
  label,
  max,
  min,
  slot,
  onResize,
}: {
  clamp: (width: number) => number;
  edge: "start" | "end";
  width: number;
  label: string;
  max: number;
  min: number;
  slot: string;
  onResize: (width: number) => void;
}) {
  const dragging = useRef(false);

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragging.current = true;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      const startX = event.clientX;
      const pane = event.currentTarget.parentElement;
      const startWidth = pane?.getBoundingClientRect().width ?? width;

      const onMove = (moveEvent: PointerEvent) => {
        if (!dragging.current) return;
        const delta = moveEvent.clientX - startX;
        const next = clamp(
          edge === "end" ? startWidth + delta : startWidth - delta
        );
        onResize(next);
      };

      const onUp = (upEvent: PointerEvent) => {
        dragging.current = false;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        try {
          event.currentTarget.releasePointerCapture?.(upEvent.pointerId);
        } catch {
          /* capture already released */
        }
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [clamp, edge, onResize, width]
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const step = event.shiftKey ? 32 : 8;
      onResize(clamp(width + (edge === "end" ? direction : -direction) * step));
    },
    [clamp, edge, onResize, width]
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemax={max}
      aria-valuemin={min}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      data-slot={slot}
      className={cn(
        "absolute inset-y-0 z-10 w-1.5 cursor-col-resize touch-none bg-transparent hover:bg-line-strong/40 active:bg-line-strong/60",
        edge === "end"
          ? "right-0 translate-x-1/2"
          : "left-0 -translate-x-1/2"
      )}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}

function SplitViewListWork({
  listCollapsed = false,
  listLabel,
  listPane,
  listResizable = false,
  listWidth: listWidthProp,
  onListCollapsedChange,
  onListWidthChange,
  posture,
  workLabel,
  workPane,
  workScroll,
}: {
  listCollapsed?: boolean;
  listLabel: string;
  listPane: ReactNode;
  listResizable?: boolean;
  listWidth?: number;
  onListCollapsedChange?: (collapsed: boolean) => void;
  onListWidthChange?: (width: number) => void;
  posture: SplitViewPosture;
  workLabel: string;
  workPane: ReactNode;
  workScroll?: "pane" | "contained";
}) {
  /*
   * Opt-in managed geometry. Call sites that never pass collapse / resize
   * keep the token width classes (`w-96` / `w-80`) so dock posture stays a
   * one-class swap.
   */
  const managedList =
    listResizable ||
    listCollapsed ||
    listWidthProp !== undefined ||
    onListCollapsedChange !== undefined ||
    onListWidthChange !== undefined;

  /*
   * Controlled only when the caller can accept updates. `listWidth` alone is
   * the initial open width (Inbox 440) — otherwise drag would no-op against a
   * frozen prop.
   */
  const controlled = onListWidthChange !== undefined;
  const postureWidth = LIST_WIDTH_DEFAULT[posture];
  const [uncontrolledWidth, setUncontrolledWidth] = useState(
    () => listWidthProp ?? postureWidth
  );
  const userSized = useRef(false);
  const prevPosture = useRef(posture);

  /*
   * Follow dock/default posture only when the posture actually changes and the
   * user has not dragged. Never clobber an initial `listWidth` seed on mount.
   */
  useEffect(() => {
    if (
      !managedList ||
      controlled ||
      userSized.current ||
      listCollapsed ||
      prevPosture.current === posture
    ) {
      prevPosture.current = posture;
      return;
    }
    prevPosture.current = posture;
    setUncontrolledWidth(postureWidth);
  }, [controlled, listCollapsed, managedList, posture, postureWidth]);

  const openWidth = clampListWidth(
    controlled && listWidthProp !== undefined
      ? listWidthProp
      : uncontrolledWidth
  );
  const width = listCollapsed ? LIST_COLLAPSED_WIDTH : openWidth;

  const setOpenWidth = useCallback(
    (next: number) => {
      const clamped = clampListWidth(next);
      userSized.current = true;
      if (!controlled) {
        setUncontrolledWidth(clamped);
      }
      onListWidthChange?.(clamped);
    },
    [controlled, onListWidthChange]
  );

  return (
    <Inline
      data-slot="split-view"
      data-posture={posture}
      data-layout="list-work"
      align="stretch"
      className="h-full min-h-0 w-full"
    >
      <Stack
        data-slot="split-view-list"
        data-collapsed={listCollapsed ? "true" : "false"}
        data-resizable={listResizable ? "true" : "false"}
        render={<aside aria-label={listLabel} />}
        style={managedList ? { width } : undefined}
        className={cn(
          LIST_PANE_CLASS,
          "min-h-0 overflow-hidden",
          !managedList && LIST_WIDTH_CLASS[posture]
        )}
      >
        {listPane}
        {listResizable && !listCollapsed ? (
          <PaneResizeHandle
            clamp={clampListWidth}
            edge="end"
            width={openWidth}
            label="Resize list sidebar"
            max={LIST_WIDTH_MAX}
            min={LIST_WIDTH_MIN}
            slot="split-view-list-resize"
            onResize={setOpenWidth}
          />
        ) : null}
      </Stack>
      <SplitViewWorkPane label={workLabel} scroll={workScroll}>
        {workPane}
      </SplitViewWorkPane>
    </Inline>
  );
}

/**
 * Two panes with roles: the work pane keeps its width, the other pane yields,
 * and neither is remounted by the posture that makes it happen.
 */
function SplitView(props: SplitViewProps) {
  const { posture = "default", workLabel, workPane, workScroll } = props;

  if (isSplitViewList(props)) {
    return (
      <SplitViewListWork
        posture={posture}
        workLabel={workLabel}
        workPane={workPane}
        workScroll={workScroll}
        listLabel={props.listLabel}
        listPane={props.listPane}
        listCollapsed={props.listCollapsed}
        listResizable={props.listResizable}
        listWidth={props.listWidth}
        onListCollapsedChange={props.onListCollapsedChange}
        onListWidthChange={props.onListWidthChange}
      />
    );
  }

  return (
    <SplitViewWorkReference
      posture={posture}
      workLabel={workLabel}
      workPane={workPane}
      workScroll={workScroll}
      referenceHidden={props.referenceHidden}
      referenceLabel={props.referenceLabel}
      referencePane={props.referencePane}
      referenceResizable={props.referenceResizable}
      referenceScroll={props.referenceScroll}
      referenceSeam={props.referenceSeam}
      referenceWidth={props.referenceWidth}
    />
  );
}

function SplitViewWorkReference({
  posture,
  referenceHidden = false,
  referenceLabel,
  referencePane,
  referenceResizable = false,
  referenceScroll,
  referenceSeam,
  referenceWidth = "context",
  workLabel,
  workPane,
  workScroll,
}: {
  posture: SplitViewPosture;
  referenceHidden?: boolean;
  referenceLabel: string;
  referencePane: ReactNode;
  referenceResizable?: boolean;
  referenceScroll?: "pane" | "contained";
  referenceSeam?: boolean;
  referenceWidth?: SplitViewReferenceWidth;
  workLabel: string;
  workPane: ReactNode;
  workScroll?: "pane" | "contained";
}) {
  const tokenWidth = REFERENCE_WIDTH_PX[referenceWidth][posture];
  const [userWidth, setUserWidth] = useState<number | null>(null);

  const openWidth = clampReferenceWidth(userWidth ?? tokenWidth);

  const onResize = useCallback((next: number) => {
    setUserWidth(clampReferenceWidth(next));
  }, []);

  return (
    <Inline
      data-slot="split-view"
      data-posture={posture}
      data-layout="work-reference"
      align="stretch"
      className="h-full min-h-0 w-full"
    >
      <SplitViewWorkPane label={workLabel} scroll={workScroll}>
        {workPane}
      </SplitViewWorkPane>
      <Stack
        data-slot="split-view-reference"
        data-hidden={referenceHidden ? "true" : undefined}
        data-resizable={referenceResizable ? "true" : "false"}
        data-scroll={referenceScroll ?? "pane"}
        render={<aside aria-label={referenceLabel} />}
        aria-hidden={referenceHidden ? true : undefined}
        style={
          referenceHidden || !referenceResizable
            ? undefined
            : { width: openWidth }
        }
        className={cn(
          REFERENCE_PANE_CLASS,
          referenceScroll === "contained" ? "min-h-0 overflow-hidden" : undefined,
          referenceHidden
            ? REFERENCE_HIDDEN_CLASS
            : [
                referenceResizable
                  ? undefined
                  : REFERENCE_WIDTH_CLASS[referenceWidth][posture],
                referenceSeam === false ? undefined : REFERENCE_SEAM_CLASS,
              ]
        )}
      >
        {referenceScroll === "contained" ? (
          referencePane
        ) : (
          <ScrollArea recoveryKey={`reference:${referenceLabel}`} className={PANE_SCROLL_CLASS}>
            {referencePane}
          </ScrollArea>
        )}
        {referenceResizable && !referenceHidden ? (
          <PaneResizeHandle
            clamp={clampReferenceWidth}
            edge="start"
            width={openWidth}
            label={`Resize ${referenceLabel}`}
            max={REFERENCE_WIDTH_MAX}
            min={REFERENCE_WIDTH_MIN}
            slot="split-view-reference-resize"
            onResize={onResize}
          />
        ) : null}
      </Stack>
    </Inline>
  );
}

export {
  ListSidebarChrome,
  ListSidebarTitle,
  SplitView,
  SPLIT_VIEW_RULES,
  LIST_COLLAPSED_WIDTH,
  LIST_SIDEBAR_CHROME_CLASS,
  LIST_WIDTH_DEFAULT,
  LIST_WIDTH_MAX,
  LIST_WIDTH_MIN,
  REFERENCE_WIDTH_MAX,
  REFERENCE_WIDTH_MIN,
};
export type { SplitViewPosture, SplitViewProps, SplitViewReferenceWidth };
