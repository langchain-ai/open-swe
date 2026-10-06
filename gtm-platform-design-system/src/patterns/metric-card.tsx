"use client";

import type { ReactNode } from "react";

import { cn } from "../lib/utils";
import { Box, Inline, Stack } from "../ui/box";
import { ChevronRight, type Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";
import { Frame, FramePanel } from "../ui/frame";

const METRIC_CARD_RULES: readonly string[] = [
  "Dashboard headline metrics are cards with an icon, a clear title, one dominant value, and one context line. Dense record metadata remains a StatReadout row or cell.",
  "A metric card grid is one ReUI Frame containing repeated FramePanel cards. Pass `columns` for the peak count (two, three, or four; four is the default). It reflows from one column up to that count instead of shrinking a strip. Pick the step that divides the set: a row of counts that does not fill its peak leaves a card stranded on its own line. That is why the three step has no two-column rung: two across strands the third at every width between the rungs, so a set of three goes from one column straight to three.",
  "The icon and title share the first band. The title names the metric, so the icon is decorative and never repeats the accessible name.",
  "Three bands, two spaces: the title band, the value, the context line. The title band sits a `md` step above the value and the context line a `sm` step under it, both on the spacing scale and both owned here, so every card in the product breathes the same way whatever grid it sits in.",
  "The value is preformatted, mono, tabular, and the card's one focal point. The pattern never rounds, abbreviates, adds units, or infers whether a change is good or bad.",
  "The context line names the window, coverage, or certainty. Omit it when that context lives in the info disclosure instead. When present it wraps to a second line and clamps there, so context is never silently clipped and card heights stay bounded.",
  "Metric cards are readings, not controls. They have no hover action, menu, link, or decorative trend badge unless a real owning route and comparison contract exist.",
  "The optional info slot is one of three sanctioned affordances: a small trigger at the end of the title band opening the metric's provenance (description, coverage note, window, denominator, reproduce path). It discloses; it never navigates.",
  "The optional sparkline slot is word-sized, sits on the value's line at the card's trailing edge, and draws the same stored series the value summarizes, so the comparison contract is real. Trailing rather than hugging the value, so the figure reads first and alone and the shape is a second glance rather than part of the number. A sparkline from any other series, or a decorative one, is the trend badge rule 6 forbids.",
  "Given onDrill, the whole card is the other sanctioned affordance: it zooms into the series its own sparkline draws, and nothing else. Whole card rather than sparkline alone, because a 96px target reads as decoration and a card that responds only in one corner teaches nothing; the cursor and the hover lift are what say it is clickable at all. It satisfies rule 6 through that comparison contract rather than despite it. Without onDrill the card stays inert, and a card with no sparkline never becomes clickable, since there would be nothing to zoom into.",
  "Given onOpen on a card with no sparkline, the whole card is the third sanctioned affordance: it opens the collection its value counts (the list of those domains, reps, or mailboxes), in a sheet over the page. A trailing ChevronRight at the end of the title band says so, because without a sparkline nothing else on the card reads as a way in. `openLabel` names the collection, not the gesture. A card with a sparkline drills into its series instead and never also opens; onOpen on it is inert.",
  "The drill and the open are each a real button covering the card, never a role on the panel. A button role on the panel would swallow the info trigger inside it: assistive technology does not expose interactive descendants of a button, so the provenance disclosure would disappear from the accessibility tree even though it still works with a mouse. The covering button and the info trigger are siblings instead, both separately focusable, and the panel carries the hover and focus styling for whichever of them the reader is on.",
];

interface MetricCardProps {
  /** Clear metric name. Rendered as the card heading. */
  title: string;
  /** Preformatted value. Formatting remains with the data owner. */
  value: string | number;
  /** Window, coverage, or certainty for this specific reading. Omit when the info slot carries it. */
  detail?: string;
  /** Product glyph shown in the standard IconWell. */
  icon: Glyph;
  /** Provenance disclosure trigger, end of the title band. See rule 7. */
  info?: ReactNode;
  /** Word-sized trend of the same stored series, beside the value. See rule 8. */
  sparkline?: ReactNode;
  /** Makes the whole card open its own series at full resolution. See rule 9. */
  onDrill?: () => void;
  /** Accessible name for the drill trigger. Names the series, not the gesture. */
  drillLabel?: string;
  /** Makes a card with no sparkline open the collection its value counts. See rule 10. */
  onOpen?: () => void;
  /** Accessible name for the open trigger. Names the collection, not the gesture. */
  openLabel?: string;
  /** How large the value is set: the title rung by default, the display rung for a page's key numbers. */
  emphasis?: "title" | "display";
}

function MetricCard({
  detail,
  drillLabel,
  emphasis = "title",
  icon,
  info,
  onDrill,
  onOpen,
  openLabel,
  sparkline,
  title,
  value,
}: MetricCardProps) {
  const drillable = sparkline !== undefined && onDrill !== undefined;
  const openable = sparkline === undefined && onOpen !== undefined;
  return (
    <FramePanel
      data-slot="metric-card"
      data-drillable={drillable ? "true" : undefined}
      data-openable={openable ? "true" : undefined}
      className={cn(
        "min-w-0 gap-3",
        (drillable || openable) &&
          "transition-[background-color,border-color,box-shadow] duration-fast ease-out-quint hover:border-line-strong hover:bg-hover has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary motion-reduce:transition-none"
      )}
    >
      {drillable ? (
        /*
         * The drill trigger covers the card and is a sibling of the info button
         * rather than its ancestor. A role on the panel would read as one control
         * to assistive technology and take the provenance trigger out of the
         * accessibility tree with it, which no amount of event handling repairs.
         */
        <Box
          render={<button type="button" />}
          data-slot="metric-card-drill"
          aria-label={drillLabel}
          onClick={onDrill}
          className="absolute inset-0 cursor-pointer rounded-(--frame-panel-radius) outline-none"
        />
      ) : null}
      {openable ? (
        /* The same covering sibling as the drill, for the same reason: the info trigger stays reachable. */
        <Box
          render={<button type="button" />}
          data-slot="metric-card-open"
          aria-label={openLabel}
          onClick={onOpen}
          className="absolute inset-0 cursor-pointer rounded-(--frame-panel-radius) outline-none"
        />
      ) : null}
      <Inline gap="sm" align="center" className="min-w-0">
        <IconWell>
          <Icon icon={icon} size="sm" />
        </IconWell>
        <Box
          render={<h3 />}
          className="min-w-0 flex-1 truncate text-label font-medium text-ink"
        >
          {title}
        </Box>
        {info === undefined ? null : (
          /* Above the covering button, so a click on it discloses rather than zooms. */
          <Box data-slot="metric-card-info" className="relative shrink-0">
            {info}
          </Box>
        )}
        {openable ? (
          <Icon icon={ChevronRight} size="sm" className="shrink-0 text-ink-subtle" />
        ) : null}
      </Inline>
      <Stack gap="sm">
        <Inline gap="sm" align="center" justify="between" className="min-w-0">
          <Box
            render={<span />}
            className={cn(
              "truncate font-mono font-medium text-ink tabular-nums",
              emphasis === "display" ? "text-display" : "text-title"
            )}
          >
            {value}
          </Box>
          {sparkline === undefined ? null : (
            /*
             * The chart takes no pointer events on a drillable card, belt to the
             * covering button's braces. Recharts repaints its surface on mousedown
             * to show a tooltip, landing mouseup on a different node, so a click it
             * did reach would never be synthesized at all.
             */
            <Box
              data-slot="metric-card-sparkline"
              className={cn("min-w-0 shrink-0", drillable && "[&_*]:pointer-events-none")}
            >
              {sparkline}
            </Box>
          )}
        </Inline>
        {detail === undefined ? null : (
          <Box render={<span />} className="line-clamp-2 text-meta text-ink-subtle">
            {detail}
          </Box>
        )}
      </Stack>
    </FramePanel>
  );
}

const GRID_COLUMNS_CLASS = {
  2: "grid gap-1 @2xl:grid-cols-2",
  3: "grid gap-1 @xl:grid-cols-3",
  4: "grid gap-1 @2xl:grid-cols-2 @4xl:grid-cols-4",
} as const;

interface MetricCardGridProps {
  /** Accessible name for the metric set. */
  label: string;
  /** Peak column count. The grid still collapses on a narrower container. */
  columns?: keyof typeof GRID_COLUMNS_CLASS;
  /** Repeated MetricCard elements. */
  children: ReactNode;
}

function MetricCardGrid({ children, columns = 4, label }: MetricCardGridProps) {
  return (
    <Frame
      data-slot="metric-card-grid"
      role="group"
      aria-label={label}
      className="@container w-full"
    >
      <Box className={GRID_COLUMNS_CLASS[columns]}>
        {children}
      </Box>
    </Frame>
  );
}

export { MetricCard, MetricCardGrid, METRIC_CARD_RULES };
export type { MetricCardGridProps, MetricCardProps };
