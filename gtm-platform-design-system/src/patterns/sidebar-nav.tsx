"use client";

/*
 * Rules for SidebarNav.
 *
 * The rules themselves are `SIDEBAR_NAV_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the nav a
 * builder reads in the gallery and the nav this file draws are the same
 * strings.
 *
 * Geometry is measured, not guessed. CORE 06 section 02 (Paper file
 * 01KYQDVRVBZ6KYPDP179BS2M3G, node QI7-0), corroborated against the live rail
 * on CORE 01 (NHU-0), and re-derived against the SOFT radius ladder
 * (5/8/12/14/20/24, Amal 2026-08-05) where the board still quotes the old one:
 *
 *   list    8px padding, 2px between rows        QID-0 / NHU-0
 *   parent  32px, radius 14, 10px inset, gap 8   QIE-0 / NJL-0
 *   child   28px, radius 12, 10px inset, gap 8   QIR-0 / NIW-0
 *   spine   16px left margin, 1px line-strong    QIQ-0 / NIA-0
 *   icon    16px, parents only                   QIF-0
 *   count   a numeral in a 20px lane             QIK-0 / NJE-0
 *   type    13px/500 ink on parents, 13px/400 ink-muted on children
 *
 * THE CHILD'S RADIUS, AND WHY IT IS NOT `control`. The board drew the child row
 * square, which is what put a hard-cornered hover fill directly under a rounded
 * one -- the tell Amal caught. Copying the parent's `control` (14) would be
 * worse than either: half of a 28px row is 14, and the ratio law (wiki 02 rule
 * 1) says a radius at or above half the smaller dimension is a circle, so a
 * 28px row on radius 14 is a pill pretending to be a row. `compact` (12) is the
 * rung the ladder already reserves for an item inside a container, it is one
 * step in from its parent so the nesting stays concentric, and it is under the
 * half. The rung is derived, not picked.
 *
 * THE CHILD'S INSET, AND THE INDENT. The child carries the parent's own 10px
 * inset rather than a smaller one, so the two rungs differ in height and in
 * where their box starts and in nothing else. The indent is the spine's job:
 * the spine hangs 16px in and the list inside it is padded 8px more, so the
 * child's fill clears the hairline by 8px instead of colliding with it, and the
 * child's label lands in the parent's label lane (a pixel under it, which is
 * where a 1px hairline in the flow puts it -- the alternative was an arbitrary
 * value, and 1px is not worth one). Both rungs' fills share a right edge, so the
 * count lane is one column top to bottom.
 *
 * HOVER IS NOT SELECTION, AND SELECTION WINS. `hover:bg-hover` is a pseudo-class
 * and `bg-selected` is not, so the browser gave hover the higher specificity and
 * a selected row repainted as a hovered row the moment a pointer crossed it.
 * The two states are now mutually exclusive by construction: a row takes the
 * hover class or the selected class, never both. And selection is not carried by
 * the fill alone, because `--gtm-selected` and `--gtm-hover` are byte-identical
 * on the dark theme (see the todo in the round report) -- it promotes ink and
 * weight as well, which is the channel that survives.
 *
 * THE TRAILING SIGNAL IS A NUMERAL OR A DOT, NEVER A CHIP. Counts are
 * unresolved work; the dot is new content since acknowledgement. A count used
 * to be a 20px chip filled with
 * `bg-selected`, which is the fill a selected row wears: the count vanished on
 * the one row that most needed to be read, and on the dark theme it vanished on
 * hover too. Rather than find it a fourth fill, it lost the fill. Containment
 * follows commitment and a count is not a committed object; the reserved lane
 * and the tabular figures already do everything the chip was doing.
 *
 * This is built from Box and Stack rather than composed over `ui/sidebar.tsx`.
 * That file is the shadcn sidebar: it owns collapse state, an off-canvas sheet,
 * a cookie, a 16rem width and a rail handle, and its `SidebarMenuSub` draws the
 * child spine as `mx-3.5 px-2.5` with a `border-line` rule, which is neither
 * the 16px spine nor the strong hairline the board specifies. Bending it would
 * have meant overriding six geometry classes per part and inheriting the
 * collapse machinery this pattern deliberately does not have. `ui/sidebar` is
 * untouched and still available for a surface that wants a collapsible shell
 * chrome; this pattern is the nav that goes inside one.
 *
 * The one disclosure it does have is `SidebarNavGroup`, and it is a different
 * object from `SidebarNavItem` rather than a flag on it: a group is a category,
 * an item is a destination, and the rule that a destination never hides itself
 * is intact precisely because the disclosure is not available to one. The motion
 * comes from `ui/collapsible` -- the `grid-template-rows` transition between
 * `0fr` and `1fr`, on the one duration and the one curve, with reduced motion
 * respected -- and the opacity half is added on the same panel so height and
 * fade run as one gesture. The gallery rail hand-rolled all of this; it lives
 * here now and no second surface has to write it again.
 */

import type { ReactNode } from "react";

import { HostLink } from "../host";
import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import { Collapsible, CollapsibleContent } from "../ui/collapsible";
import { ChevronRight, type Glyph } from "../ui/glyphs";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "../ui/hover-card";
import { Icon } from "../ui/icon";
import { Separator } from "../ui/separator";

const SIDEBAR_NAV_RULES: readonly string[] = [
  "Selection is a fill, ink and weight. Never a border, never a shadow, never a left bar. A selected row is the same height in the same place with `bg-selected` behind it, at `text-ink` on the medium step, so the rail does not reflow and no second indicator has to be kept in sync with the first. The type half is not decoration: `--gtm-selected` and `--gtm-hover` resolve to the same value on the dark theme, so a row that said selection with the fill alone said nothing there.",
  "A selected row does not answer the pointer. Hover and selected are mutually exclusive class sets, not two layers: `hover:bg-hover` outranks `bg-selected` on specificity, so a row that carried both repainted as merely-hovered the moment a pointer crossed the thing you were standing on.",
  "Two rungs, and only two destination sizes: parent rows are 32px on radius 14, child rows are 28px on radius 12. Both take the same 10px inset, so height and position are the only difference between them. The child's radius is derived, not copied: half of 28 is 14, and the ratio law calls a radius at or above half the smaller dimension a circle, so `compact` is the rung and `control` would be a pill. A family that will grow (Admin, later Team) takes `SidebarNavCluster`'s `label`, the third size: meta, ink-subtle, not pressable, never an icon, never a destination.",
  "Parents carry icons. Children carry the spine, and may also carry a 16px glyph when the destination needs its own mark (Alerts, Meetings, …). The spine still says nesting; the optional child icon names the job. The list inside the spine is padded 8px so the child's fill clears the hairline rather than landing on it.",
  "The reserved 20px trailing lane carries one quiet attention signal. A numeral means unresolved, actionable work and does not clear merely because the destination opened. A 6px primary dot means new content since the server-owned acknowledgement and clears when the destination is opened. Zero, loading and unavailable render nothing. Never aggregate a child's signal onto its parent: duplicate totals create noise without adding information.",
  "Counts are numerals on tabular figures, never chips. The chip's fill was the selected fill, so a count disappeared into the row it most needed to be legible on. Counts publish 99+ rather than growing the lane. In a collapsed icon rail any attention compresses to the same 6px dot; the expanded rail and contextual preview carry the exact count.",
  "Depth stops here. The rail expresses one level of nesting and no more; anything deeper is an object route, and depth on an object route is the lineage band's job (see PageBand). A third level in the sidebar is the signal that a surface needed a band and did not get one.",
  "A destination never hides *itself*. The product rail keeps children visible; omit `open` / `onOpenChange` so the spine stays mounted. Disclosure belongs to `SidebarNavGroup` on an index rail, not to the product map. A heading-only family (Admin) is a section label plus destinations, never a parent row that only exists to hold one child. Role-gated families follow a hairline after daily work so they can grow without joining Overview.",
  "A group's chevron IS its leading glyph, and its state is controlled by the surface. The 16px slot holds one thing: a destination spends it on its glyph, a disclosure spends it on its chevron, and neither grows a second leading lane. Open state lives with whoever owns the URL, so a pasted link can decide what is expanded.",
  "The agent is a sidebar row plus Cmd-J, never a header button. It was already duplicated by the rail, and the button was what made a permanent header look necessary.",
  "Icons here are navigation, not decoration: one glyph per destination, at the 16px slot, from the closed glyph list. A rail where some parents have icons and others do not is a rail with two lanes.",
];

/* QID-0 / NHU-0: 8px around the list, 2px between rows. `flush` drops the pad. */
const NAV_LIST_CLASS = "w-full gap-0.5 p-2";
const NAV_LIST_FLUSH_CLASS = "w-full gap-0.5";

/* What both rungs share: pressable behaviour and type step, no geometry. */
const ROW_CLASS =
  "w-full text-left text-label transition-colors duration-fast ease-out-quint outline-none focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none";

/* QIE-0 / NJL-0: 32px, radius 14, 10px inset, 8px gap, 13px/500 on ink. */
const PARENT_ROW_CLASS = "h-control rounded-control px-2.5 font-medium text-ink";

/* QIR-0 / NIW-0: 28px, radius 12, the parent's 10px inset, 13px/400 muted. */
const CHILD_ROW_CLASS = "h-control-sm rounded-compact px-2.5 text-ink-muted";

/* Only an unselected row answers the pointer. A child lifts its ink with it. */
const PARENT_HOVER_CLASS = "hover:bg-hover";
const CHILD_HOVER_CLASS = "hover:bg-hover hover:text-ink";

/* One answer for both rungs, and it is three channels rather than one fill. */
const SELECTED_CLASS = "bg-selected font-medium text-ink";

/* The 2px between a parent and its own child group, same rhythm as the list. */
const ITEM_STACK_CLASS = "gap-0.5";

/* A group is the same stack, but the disclosure root owns the column itself. */
const GROUP_CLASS = "flex w-full flex-col gap-0.5";

/* QIQ-0 / NIA-0: the spine, 16px in, on the strong hairline, then 8px of air. */
const SPINE_CLASS = "ml-4 gap-0.5 border-l border-line-strong pl-2";

/*
 * The fade, added to the height transition `ui/collapsible` already runs. Both
 * halves ride the same panel so they are one gesture, and the closed and
 * starting states are named rather than the base class being flipped -- see the
 * note in `ui/collapsible.tsx` for why the other way makes the panel snap.
 */
const DISCLOSURE_CLASS =
  "transition-[grid-template-rows,opacity] data-closed:opacity-0 data-starting-style:opacity-0";

/*
 * The chevron's turn. `transition-[rotate]`, never `transition-transform`:
 * Tailwind v4 compiles `rotate-90` to the standalone `rotate` property, so a
 * transition naming `transform` animates nothing at all.
 */
const CHEVRON_CLASS =
  "text-ink-subtle transition-[rotate] duration-fast ease-out-quint motion-reduce:transition-none";
const CHEVRON_OPEN_CLASS = "rotate-90";

/* QIK-0 / NJE-0: the figure itself, on meta type and tabular figures. */
const COUNT_CLASS = "text-meta text-ink-subtle tabular-nums";
const ATTENTION_DOT_CLASS = "size-1.5 rounded-full bg-primary";

/* Reserved on every row, count or no count, so the numbers form one lane. */
const COUNT_LANE_CLASS = "ml-auto min-w-badge shrink-0";

interface SidebarNavRowProps {
  label: string;
  /** Renders an anchor; without it the row is a button. */
  href?: string;
  onSelect?: () => void;
  /** The fill, the ink and the weight, and `aria-current`. One row per rail. */
  selected?: boolean;
  /** Unread or open work behind this destination. Zero is not a count. */
  count?: number;
  /** The source has more work than the bounded count can publish. */
  countOverflow?: boolean;
  /** New content since acknowledgement. Mutually exclusive with count. */
  attentionDot?: boolean;
}

/** The trailing lane. Always rendered, so counts line up down the rail. */
function CountLane({
  attentionDot = false,
  count,
  countOverflow = false,
}: Pick<SidebarNavRowProps, "attentionDot" | "count" | "countOverflow">) {
  return (
    <Inline justify="end" align="center" className={COUNT_LANE_CLASS}>
      {count === undefined ? null : (
        <Box
          render={<span />}
          data-slot="sidebar-nav-count"
          className={COUNT_CLASS}
        >
          {countOverflow ? `${count}+` : count}
        </Box>
      )}
      {count === undefined && attentionDot ? (
        <Box
          render={<span />}
          aria-hidden
          data-slot="sidebar-nav-attention-dot"
          className={ATTENTION_DOT_CLASS}
        />
      ) : null}
    </Inline>
  );
}

/** The row body, shared by all three rungs: pressable, aria, and its state set. */
function NavRow({
  attentionDot = false,
  className,
  count,
  countOverflow = false,
  expanded,
  hoverClassName,
  href,
  label,
  leading,
  onSelect,
  selected = false,
  slot,
}: SidebarNavRowProps & {
  className: string;
  /** The class set a row wears while it is NOT selected. Never both. */
  hoverClassName: string;
  slot: string;
  leading?: ReactNode;
  /** Set only on a disclosure row, where it is the row's whole announcement. */
  expanded?: boolean;
}) {
  const element =
    href === undefined ? (
      <button type="button" onClick={onSelect} />
    ) : (
      <HostLink href={href} onClick={onSelect} />
    );

  return (
    <Inline
      data-slot={slot}
      data-selected={selected}
      render={element}
      gap="sm"
      align="center"
      aria-current={selected ? "page" : undefined}
      aria-expanded={expanded}
      className={cn(
        ROW_CLASS,
        className,
        selected ? SELECTED_CLASS : hoverClassName
      )}
    >
      {leading}
      <Box render={<span />} className="min-w-0 flex-1 truncate">
        {label}
      </Box>
      <CountLane
        count={count}
        countOverflow={countOverflow}
        attentionDot={attentionDot}
      />
    </Inline>
  );
}

interface SidebarNavChildProps extends SidebarNavRowProps {
  /** Optional 16px mark for the destination; spine still owns nesting. */
  icon?: Glyph;
}

/**
 * A child destination: 28px on radius 12, hanging off the spine its parent
 * draws. Optional icon names the job without replacing the spine.
 */
function SidebarNavChild({ icon, ...props }: SidebarNavChildProps) {
  return (
    <Box render={<li />}>
      <NavRow
        {...props}
        className={CHILD_ROW_CLASS}
        hoverClassName={CHILD_HOVER_CLASS}
        slot="sidebar-nav-child"
        leading={icon ? <Icon icon={icon} size="sm" /> : undefined}
      />
    </Box>
  );
}

/** The spine and the list of children on it, shared by the item and the group. */
function ChildSpine({ children }: { children: ReactNode }) {
  return (
    <Stack
      data-slot="sidebar-nav-spine"
      render={<ul />}
      gap="none"
      className={SPINE_CLASS}
    >
      {children}
    </Stack>
  );
}

interface SidebarNavItemProps extends SidebarNavRowProps {
  /** Parents carry icons; the prop is required so no rail grows two lanes. */
  icon: Glyph;
  /** SidebarNavChild rows under an optional disclosure. */
  children?: ReactNode;
  /**
   * Icon-only row for the 48px collapsed product rail. Label stays the
   * accessible name; any exact attention count compresses to a dot.
   */
  compact?: boolean;
  /** Context card for the icon-only row. Expanded destinations show their labels directly. */
  preview?: ReactNode;
  /** Lets the host start and stop a shared, lazy preview read. */
  onPreviewOpenChange?: (open: boolean) => void;
  /**
   * When children are present, controlled disclosure. Omit both to keep
   * children always expanded (specimen / always-on nesting).
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/** A top level destination: 32px on radius 14, with optional nested children. */
function SidebarNavItem({
  attentionDot = false,
  children,
  compact = false,
  count,
  countOverflow = false,
  icon,
  label,
  open,
  onOpenChange,
  onPreviewOpenChange,
  preview,
  ...row
}: SidebarNavItemProps) {
  if (compact) {
    const hasAttention = count !== undefined || attentionDot;
    const accessibleLabel = count !== undefined
      ? `${label}, ${countOverflow ? `${count}+` : count} need attention`
      : attentionDot
        ? `${label}, new`
        : label;
    const compactRow = (
      <Inline
        data-slot="sidebar-nav-item"
        data-selected={row.selected}
        data-compact="true"
        render={
          row.href === undefined ? (
            <button type="button" onClick={row.onSelect} />
          ) : (
            <HostLink href={row.href} onClick={row.onSelect} />
          )
        }
        justify="center"
        align="center"
        aria-label={accessibleLabel}
        aria-current={row.selected ? "page" : undefined}
        className={cn(
          ROW_CLASS,
          PARENT_ROW_CLASS,
          "px-0",
          row.selected ? SELECTED_CLASS : PARENT_HOVER_CLASS
        )}
      >
        <Box className="relative">
          <Icon icon={icon} />
          {hasAttention ? (
            <Box
              render={<span />}
              aria-hidden
              data-slot="sidebar-nav-compact-attention-dot"
              className="absolute -top-0.5 -right-0.5 size-1.5 rounded-full bg-primary ring-2 ring-canvas"
            />
          ) : null}
        </Box>
      </Inline>
    );

    return (
      <Box render={<li />}>
        {preview === undefined ? (
          compactRow
        ) : (
          <HoverCard onOpenChange={onPreviewOpenChange}>
            <HoverCardTrigger render={compactRow} />
            <HoverCardContent side="right" align="start" sideOffset={6}>
              {preview}
            </HoverCardContent>
          </HoverCard>
        )}
      </Box>
    );
  }

  const disclosure =
    children !== undefined && open !== undefined && onOpenChange !== undefined;

  if (!disclosure) {
    return (
      <Stack render={<li />} gap="none" className={ITEM_STACK_CLASS}>
        <NavRow
          {...row}
          attentionDot={attentionDot}
          count={count}
          countOverflow={countOverflow}
          label={label}
          className={PARENT_ROW_CLASS}
          hoverClassName={PARENT_HOVER_CLASS}
          slot="sidebar-nav-item"
          leading={<Icon icon={icon} />}
        />
        {children === undefined ? null : <ChildSpine>{children}</ChildSpine>}
      </Stack>
    );
  }

  return (
    <Collapsible
      render={<li />}
      className={ITEM_STACK_CLASS}
      open={open}
      onOpenChange={onOpenChange}
    >
      {/*
       * Icon + label stay in the same leading lane as Home / Inbox (left-
       * aligned). Chevron trails so disclosure does not shift the glyph column.
       */}
      <Inline
        data-slot="sidebar-nav-item"
        data-selected={row.selected}
        data-expanded={open ? "true" : "false"}
        gap="xs"
        align="center"
        justify="start"
        className={cn(
          ROW_CLASS,
          PARENT_ROW_CLASS,
          "pr-1",
          row.selected ? SELECTED_CLASS : PARENT_HOVER_CLASS
        )}
      >
        <Inline
          gap="sm"
          align="center"
          render={
            row.href === undefined ? (
              <button type="button" onClick={row.onSelect} />
            ) : (
              <HostLink href={row.href} onClick={row.onSelect} />
            )
          }
          aria-current={row.selected ? "page" : undefined}
          className="min-w-0 flex-1 justify-start truncate outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-compact"
        >
          <Icon icon={icon} />
          <Box render={<span />} className="min-w-0 flex-1 truncate text-left">
            {label}
          </Box>
        </Inline>
        <Box
          render={
            <button
              type="button"
              aria-label={open ? `Collapse ${label}` : `Expand ${label}`}
              aria-expanded={open}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onOpenChange(!open);
              }}
            />
          }
          className="inline-flex size-control-sm shrink-0 items-center justify-center rounded-compact text-ink-subtle transition-[background-color,color] duration-fast ease-out-quint hover:bg-hover hover:text-ink outline-none focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none"
        >
          <Icon
            icon={ChevronRight}
            size="sm"
            className={cn(CHEVRON_CLASS, open ? CHEVRON_OPEN_CLASS : undefined)}
          />
        </Box>
      </Inline>
      <CollapsibleContent className={DISCLOSURE_CLASS}>
        <ChildSpine>{children}</ChildSpine>
      </CollapsibleContent>
    </Collapsible>
  );
}

interface SidebarNavGroupProps {
  /** The category name. A group is not a destination, so it takes no href. */
  label: string;
  /** How many rows are inside it. Zero is not a count. */
  count?: number;
  /** Controlled: whoever owns the URL owns which groups are open. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** SidebarNavChild rows, revealed by the disclosure. */
  children: ReactNode;
}

/**
 * A category on an index rail: the parent row toggles its children instead of
 * navigating, and the 16px leading slot carries the chevron rather than a glyph.
 */
function SidebarNavGroup({
  children,
  count,
  label,
  onOpenChange,
  open,
}: SidebarNavGroupProps) {
  return (
    <Collapsible
      render={<li />}
      className={GROUP_CLASS}
      open={open}
      onOpenChange={onOpenChange}
    >
      <NavRow
        label={label}
        count={count}
        expanded={open}
        className={PARENT_ROW_CLASS}
        hoverClassName={PARENT_HOVER_CLASS}
        slot="sidebar-nav-group"
        leading={
          <Icon
            icon={ChevronRight}
            className={cn(CHEVRON_CLASS, open ? CHEVRON_OPEN_CLASS : undefined)}
          />
        }
        onSelect={() => onOpenChange(!open)}
      />
      <CollapsibleContent className={DISCLOSURE_CLASS}>
        <ChildSpine>{children}</ChildSpine>
      </CollapsibleContent>
    </Collapsible>
  );
}

const SECTION_LABEL_CLASS =
  "px-2.5 pb-1 pt-1 text-meta font-medium text-ink-subtle";

interface SidebarNavSectionProps {
  /** The family name. A section is not a destination. */
  label: string;
}

/** Quiet heading for a family that will grow. Not pressable, not a row. */
function SidebarNavSection({ label }: SidebarNavSectionProps) {
  return (
    <Box
      data-slot="sidebar-nav-section"
      render={<h2 />}
      className={SECTION_LABEL_CLASS}
    >
      {label}
    </Box>
  );
}

interface SidebarNavClusterProps {
  children: ReactNode;
  /** Optional family name rendered as SidebarNavSection. */
  label?: string | null;
  /** Hairline above a role-gated family after daily work. */
  divider?: boolean;
}

/** One labeled family in the product rail. */
function SidebarNavCluster({
  children,
  divider = false,
  label,
}: SidebarNavClusterProps) {
  return (
    <Stack
      data-slot="sidebar-nav-cluster"
      data-divider={divider ? "true" : undefined}
      render={<li />}
      gap="none"
      className={divider || label ? "pt-3" : "pt-0.5 first:pt-0"}
    >
      {divider ? (
        <Separator className="mb-2" />
      ) : null}
      {label ? <SidebarNavSection label={label} /> : null}
      <Stack render={<ul />} className="gap-0.5">
        {children}
      </Stack>
    </Stack>
  );
}

interface SidebarNavProps {
  /** Names the landmark. "Primary" on the app rail. */
  label: string;
  /** SidebarNavItem, SidebarNavGroup, or SidebarNavCluster rows. */
  children: ReactNode;
  /**
   * Drop the list's own `p-2` when a parent already owns chrome pad
   * (AgentThreadRail). Default keeps the product-rail inset.
   */
  flush?: boolean;
}

/** The navigation rail's list: one level of nesting, two row rungs, one lane. */
function SidebarNav({ children, flush = false, label }: SidebarNavProps) {
  return (
    <Stack
      data-slot="sidebar-nav"
      data-flush={flush ? "true" : undefined}
      render={<nav aria-label={label} />}
      gap="none"
    >
      <Stack
        render={<ul />}
        gap="none"
        className={flush ? NAV_LIST_FLUSH_CLASS : NAV_LIST_CLASS}
      >
        {children}
      </Stack>
    </Stack>
  );
}

export {
  SidebarNav,
  SidebarNavChild,
  SidebarNavCluster,
  SidebarNavGroup,
  SidebarNavItem,
  SIDEBAR_NAV_RULES,
};
export type {
  SidebarNavChildProps,
  SidebarNavClusterProps,
  SidebarNavGroupProps,
  SidebarNavItemProps,
  SidebarNavProps,
};
