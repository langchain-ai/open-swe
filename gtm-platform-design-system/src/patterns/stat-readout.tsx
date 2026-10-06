"use client";

/*
 * Rules for StatReadout.
 *
 * The rules themselves are `STAT_READOUT_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern.
 *
 * Geometry is measured, not guessed. Paper file 01KYQDVRVBZ6KYPDP179BS2M3G,
 * surveyed 2026-08-05 (docs/plan/gtm-agent-product/paper-survey-2026-08-05.md,
 * "StatReadout / metric display"). Dense record numbers appear on six settled
 * surface boards in exactly three shapes:
 *
 *   signal   28px row, label left, value right, gap 10   RPV-0 / RPX-0 (CORE 08)
 *   field    40px row, one hairline underneath           O3P-0 / O0A-0 (CORE 04)
 *   cell     66px grid cell, label over value, 8px pad   NSP-0 / NSZ-0 (CORE 03)
 *   grid     4 columns, gap 6, top+bottom hairlines      NSP-0 container
 *   value    13px / weight 500 / IBM Plex Mono           all six boards
 *   label    11-12px sans, --oct-text-3                  all six boards
 *   empty    em-dash in --oct-border-strong              R3F-0 (CORE 10)
 *   tone     semantic colour on the VALUE only           RPX-0 (--oct-red)
 *
 * Two departures from the measurement, stated rather than hidden. The label is
 * 12px (`text-meta`) and not 11px, because 11px is not on the CORE 14 type
 * ladder and the survey flags the sub-12px rail rung as an open designer
 * question, not as sanctioned drift. The 66px cell is `h-16.5`, the same
 * numeric-utility escape `QueueRow` uses for its 110px badge lane, because the
 * density ladder is a row ladder and has no 66 rung.
 *
 * What the code enforces: three dense shapes and no fourth, the mono value type,
 * tone on the value only, the em-dash empty state, and a delta that carries a
 * sign glyph beside its colour. Dashboard headline KPIs use MetricCard.
 */

import type { ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";

const STAT_READOUT_RULES: readonly string[] = [
  "StatReadout is the dense number pattern for record chrome, metadata columns, and compact run summaries. Six settled boards put those values in a label/value pair at 28, 40, or 66px.",
  "Dashboard headline KPIs use MetricCard instead: an icon-led card with a clear title, dominant value, and context line. Do not stretch StatReadout into an analytics strip or add a fourth readout shape.",
  "The value is 13px, weight 500, IBM Plex Mono, tabular. Mono is not decoration here: it is what makes a column of numbers stack, and tabular figures are what stop a value jittering as digits change under a poll. The chart tooltip uses the identical type for the identical reason.",
  "The label is the meta rung in subtle ink, and it is a noun, not a sentence. 'Open pipeline', not 'How much pipeline is open'. The pair is read as a unit, so a label long enough to wrap has already lost the shape.",
  "Semantic tone colours the value and nothing else. Not the label, not the cell, not a background. Tone is a claim that this number is good or bad; a tinted cell makes that claim about the whole readout, and a tinted label makes it about the metric rather than the reading.",
  "Empty is an em-dash, in the strong hairline ink. Never a zero, never 'N/A', never a blank: zero is a fact and absence is not, and a rep acting on a fabricated zero is the failure this rule exists to prevent.",
  "A delta carries a sign glyph as well as a tone. Colour alone fails for anyone who cannot separate the two hues, and it fails again in a screenshot pasted into Slack. The direction glyph is the affordance; the tone is the emphasis on top of it.",
  "Direction is not sentiment. Rising churn is `risk` and falling spend may be `positive`, so the delta takes its tone from the caller and never derives it from the arrow. A component that guesses will be wrong on exactly the metrics that matter.",
  "Values arrive formatted. The component never rounds, abbreviates, or adds a unit, because three surfaces will otherwise invent three million-abbreviations. Formatting belongs to the data layer, next to the thing that knows what the number means.",
  "Shape is placement, not preference. `signal` is the 28px rail row, `field` is the 40px key/value row with its hairline, `cell` is the 66px grid tile inside `StatReadoutGrid`. There is no fourth shape and no size prop; a surface that wants a bigger number wants a different pattern.",
  "Provisional is a claim about certainty, not absence. The em-dash means the value is missing; `provisional` means the value is present but not yet certified (Campaign Studio rates before funnel parity). It renders once as a `text-meta` sentence-case note beside the value — never an 11px footnote, never a caps kicker, never a fourth badge tier. (`surface-decisions` + annex 06.)",
  "Nothing here animates. Not the value on change, not the delta on mount. A number that counts up is a number a rep cannot read yet, and these readouts sit on surfaces that poll.",
];

/* The three shapes. Signal is the 28px control rung, field the 40px data rung,
 * cell the measured 66px (4px * 16.5) the ladder has no rung for. */
const SHAPE_CLASS = {
  signal: "h-control-sm w-full",
  field: "h-row-data w-full border-b border-line",
  cell: "h-16.5 w-full p-2",
} as const;

/* Tone lands on the value, never on the row. */
const TONE_CLASS = {
  neutral: "text-ink",
  positive: "text-positive",
  attention: "text-attention",
  risk: "text-risk",
  info: "text-info",
} as const;

/* 13px / 500 / mono / tabular. The one value type in the product. */
const VALUE_CLASS = "truncate font-mono text-label font-medium tabular-nums";

/* The meta rung in subtle ink. Never toned, never weighted. */
const LABEL_CLASS = "truncate text-meta text-ink-subtle";

/* Absence, drawn: the em-dash sits on the strong hairline, below body ink. */
const EMPTY_CLASS = "text-line-strong";
const EMPTY_MARK = "—";

/* The delta rides at the meta rung so it cannot outweigh the value it qualifies. */
const DELTA_CLASS = "shrink-0 font-mono text-meta tabular-nums";

/* The non-colour affordance. U+2212 rather than a hyphen so it aligns in mono. */
const DIRECTION_MARK = {
  up: "+",
  down: "−",
  flat: "·",
} as const;

/* Spoken to assistive tech, which cannot see either the glyph or the tone. */
const DIRECTION_LABEL = {
  up: "up",
  down: "down",
  flat: "flat",
} as const;

/* 4 columns at gap 6, bounded top and bottom. The cell shape's only container. */
const GRID_CLASS = "grid w-full gap-1.5 border-y border-line";
const GRID_COLUMNS_CLASS = {
  2: "grid-cols-2",
  3: "grid-cols-3",
  4: "grid-cols-4",
} as const;

type StatReadoutShape = keyof typeof SHAPE_CLASS;
type StatTone = keyof typeof TONE_CLASS;
type StatDirection = keyof typeof DIRECTION_MARK;

interface StatDelta {
  /** The sign glyph the reader sees. Never derived from the value. */
  direction: StatDirection;
  /** Pre-formatted: "12%", "3 accounts". The pattern adds only the sign. */
  value: string;
  /** The caller's claim about the change. Rising churn is `risk`. */
  tone?: StatTone;
}

interface StatReadoutProps {
  /** The metric, as a noun. "Open pipeline", "Replies this week". */
  label: string;
  /** Pre-formatted by the data layer. Null, undefined, or "" renders the em-dash. */
  value?: string | number | null;
  /** 28px rail row, 40px key/value row, or 66px grid cell. */
  shape?: StatReadoutShape;
  /** Colours the value only. */
  tone?: StatTone;
  /** Sign glyph plus tone. Never colour alone. */
  delta?: StatDelta;
  /**
   * Value is present but not certified. Renders a meta note — never invents a
   * fourth badge tier. Distinct from empty (em-dash).
   */
  provisional?: boolean | string;
}

/** The change qualifier: a sign glyph, a spoken direction, and a tone. */
function StatDeltaMark({ delta }: { delta: StatDelta }) {
  const tone: StatTone = delta.tone === undefined ? "neutral" : delta.tone;
  return (
    <Box
      data-slot="stat-readout-delta"
      data-direction={delta.direction}
      render={<span />}
      className={cn(DELTA_CLASS, TONE_CLASS[tone])}
    >
      <Box render={<span />} className="sr-only">
        {DIRECTION_LABEL[delta.direction]}{" "}
      </Box>
      {DIRECTION_MARK[delta.direction]}
      {delta.value}
    </Box>
  );
}

/**
 * One label/value pair in the three shapes the boards settle: a 28px signal
 * row, a 40px key/value row, or a 66px grid cell. Never a KPI tile.
 */
function StatReadout({
  delta,
  label,
  provisional = false,
  shape = "signal",
  tone = "neutral",
  value,
}: StatReadoutProps) {
  const empty = value === undefined || value === null || value === "";
  const provisionalNote =
    provisional === false
      ? null
      : provisional === true
        ? "Provisional"
        : provisional;

  const labelNode = (
    <Box
      data-slot="stat-readout-label"
      render={<span />}
      className={LABEL_CLASS}
    >
      {label}
    </Box>
  );

  const valueNode = (
    <Inline data-slot="stat-readout-value-lane" gap="xs" align="baseline">
      <Box
        data-slot="stat-readout-value"
        data-empty={empty}
        render={<span />}
        className={cn(VALUE_CLASS, empty ? EMPTY_CLASS : TONE_CLASS[tone])}
      >
        {empty ? EMPTY_MARK : value}
      </Box>
      {delta === undefined ? null : <StatDeltaMark delta={delta} />}
      {provisionalNote === null || empty ? null : (
        <Box
          data-slot="stat-readout-provisional"
          render={<span />}
          className="shrink-0 text-meta text-ink-subtle"
        >
          {provisionalNote}
        </Box>
      )}
    </Inline>
  );

  if (shape === "cell") {
    return (
      <Stack
        data-slot="stat-readout"
        data-shape={shape}
        gap="none"
        justify="between"
        className={SHAPE_CLASS.cell}
      >
        {labelNode}
        {valueNode}
      </Stack>
    );
  }

  return (
    <Inline
      data-slot="stat-readout"
      data-shape={shape}
      gap="sm"
      align="center"
      justify="between"
      className={SHAPE_CLASS[shape]}
    >
      {labelNode}
      {valueNode}
    </Inline>
  );
}

interface StatReadoutGridProps {
  /** Names the group for assistive tech. "Run snapshot", "Account totals". */
  label: string;
  /** 2, 3, or 4. The boards draw 4; wider than that is a table. */
  columns?: keyof typeof GRID_COLUMNS_CLASS;
  /** `StatReadout` elements on the `cell` shape. */
  children: ReactNode;
}

/**
 * The container the 66px cell shape lives in: an even grid at gap 6, bounded by
 * one hairline above and one below. The hairlines are the whole containment
 * budget; a border on all four sides would make the strip a panel.
 */
function StatReadoutGrid({
  children,
  columns = 4,
  label,
}: StatReadoutGridProps) {
  return (
    <Box
      data-slot="stat-readout-grid"
      role="group"
      aria-label={label}
      className={cn(GRID_CLASS, GRID_COLUMNS_CLASS[columns])}
    >
      {children}
    </Box>
  );
}

export { StatReadout, StatReadoutGrid, STAT_READOUT_RULES };
export type {
  StatDelta,
  StatDirection,
  StatReadoutGridProps,
  StatReadoutProps,
  StatReadoutShape,
  StatTone,
};
