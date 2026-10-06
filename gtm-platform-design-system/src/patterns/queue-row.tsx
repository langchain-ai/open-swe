"use client";

/*
 * Rules for QueueRow.
 *
 * The rules themselves are `QUEUE_ROW_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the row a
 * builder reads in the gallery and the row this file draws are the same
 * strings.
 *
 * Geometry is measured, not guessed. Paper file 01KYQDVRVBZ6KYPDP179BS2M3G,
 * CORE 08 Alerts (RIM-0) is the board that carries both densities in one panel,
 * with CORE 02 Unified Inbox (NKE-0) and CORE 04 Campaign Studio (NZV-0)
 * corroborating the leading lanes:
 *
 *   row, record   44px, 14px inset, 1px bottom rule       RN1-0
 *   row, data     40px, 14px inset, 1px bottom rule       RR0-0
 *   identity      flex-1, min-w-0; stacks at 2px on the   RN2-0 (record)
 *                 record rung, inlines at 7px on data     RR1-0 (data)
 *   primary       13px/500 ink; 600 when unread           RN3-0 / RR2-0 / NOA-0
 *   secondary     12px/400 ink-subtle on record,          RN4-0 (record)
 *                 13px/400 ink-subtle on data             RR3-0 (data)
 *   state dot     10px, marginRight 8, tone fill          O3D-0 / O31-0
 *   badge lane    110px, badge 20px on radius 6           RN5-0 / RN6-0
 *   meta lane     100px, right aligned, 13px/400 subtle   RN9-0
 *   selection     the `selected` fill, no other mark      NO2-0 vs NNR-0
 *
 * Two numbers are not on these boards and are stated here rather than invented
 * quietly. The checkbox lane is 16px because that is what `ui/checkbox.tsx`
 * measures (no board mounts a triage checkbox yet; the lane comes from the
 * ReUI inbox archetype in docs/reference/reui-blocks-review.md). The action
 * lane is 28px, the compact control rung, because wiki 02 sizes a control by
 * placement and this one stands inside a row.
 *
 * What the code enforces: fixed lane widths, two heights, one badge slot, one
 * action slot, the listbox/option pair that makes `aria-selected` legal, and
 * the touch fallback for the hover-revealed action.
 * What the rules state: that a queue is rows and never cards, that the second
 * action belongs somewhere else, and that nothing here animates.
 */

import type { KeyboardEvent, ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { Checkbox } from "../ui/checkbox";
import { cn } from "../ui/cn";
import { useTouchPrimary } from "../lib/use-touch-primary";

const QUEUE_ROW_RULES: readonly string[] = [
  "Lanes are fixed-width so the columns line up down the queue: 16px checkbox, 10px state dot, the flexible identity, 110px badge, 100px meta, 28px action. A lane that sizes itself to its contents makes every row a different shape, and a rep scanning forty rows is reading columns, not rows.",
  "Reserve the lane, fill it conditionally. The state and action lanes are mounted on every row whether or not that row has a dot or an action, because a list that reflows as items are read or hovered is a list nobody can aim at.",
  "One action, revealed on hover and on focus, and it is an icon button. The second verb is not a second button: it belongs to the row's own affordances (the detail pane it opens, the row menu, the keyboard). Two trailing buttons is the moment a queue turns into a toolbar per row.",
  "A hover-revealed action must have a non-hover path, or it is not an action. There is no hover on a touch-primary device, so the row asks `useTouchPrimary` and simply keeps the action visible there; the lane was already reserved, so nothing moves and nothing is added. Any affordance anywhere that only appears on `:hover` owes the same answer.",
  "A queue is rows, never cards. No panel, no radius, no shadow per item; separation is the one hairline underneath. Boxing each row spends the containment signal on the thing that needs it least, and CORE 07 settles containment by commitment, not by list membership.",
  "Selection is a fill plus `aria-selected`, and nothing else. Never a border, never a left bar, never a shadow: the row stays the same height in the same place, so the queue does not reflow and there is no second indicator to keep in sync with the first.",
  "Unread is weight plus the dot, never colour alone. The row already spends colour on the state badge, so a colour-only unread cue would compete with the one signal that is supposed to mean something, and it would vanish for anyone who cannot separate the two hues.",
  "Two text rows maximum, and density picks the shape: the data rung is one line, the record rung stacks the identity over its qualifier. A third line is the request for a detail pane, not for a taller row.",
  "One badge lane and one meta lane. A second value column means the surface wanted a table, and a table is `FilterableTable` with the data grid under it, never a queue with extra lanes bolted on.",
  "Density is placement, not preference. `data` is the 40px rung for scanning volume, `record` is the 44px rung when each row carries an identity and a reason. There is no third rung and no user-facing density switch.",
  "Nothing in a queue row animates. The fill flips instantly on hover and the action appears instantly, matching `ui/dropdown-menu.tsx`, where the popup transitions and the item under the pointer does not. A 160ms tint smears when a pointer sweeps a two-hundred-row queue.",
];

/* RN1-0 / RR0-0: 14px inset, one hairline underneath, the hover and selected fills. */
const ROW_CLASS =
  "group/queue-row w-full shrink-0 border-b border-line px-3.5 text-left outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary";

/* The two rungs: RR0-0 is 40px, RN1-0 is 44px. There is no third. */
const DENSITY_CLASS = {
  data: "h-row-data",
  record: "h-row-record",
} as const;

/* ui/checkbox.tsx is a 16px square; the lane is the checkbox and no wider. */
const SELECT_LANE_CLASS = "w-4 shrink-0";

/* O3D-0 / O31-0: a 10px dot. The lane is mounted on every row, dot or no dot. */
const STATE_LANE_CLASS = "w-2.5 shrink-0";
const STATE_DOT_CLASS = "size-2.5 rounded-full bg-primary";

/* RN2-0 / RR1-0: the one flexible region, and the only one allowed to be. */
const IDENTITY_CLASS = "min-w-0 flex-1";

/* RN2-0: 2px between the two lines on the record rung. */
const IDENTITY_STACK_CLASS = "gap-0.5";

/* RN3-0 / RR2-0: 13px/500 on ink. NOA-0: 600 when the row is unread. */
const PRIMARY_CLASS = "min-w-0 truncate text-label text-ink";
const PRIMARY_READ_CLASS = "font-medium";
const PRIMARY_UNREAD_CLASS = "font-semibold";

/* RN4-0 (record, 12px) and RR3-0 (data, 13px): the muted line, both densities. */
const MUTED_CLASS = {
  data: "text-label text-ink-subtle",
  record: "text-meta text-ink-subtle",
} as const;

/* The qualifier never truncates; the summary is what gives way. */
const SECONDARY_CLASS = "shrink-0 whitespace-nowrap";
const SUMMARY_CLASS = "min-w-0 flex-1 truncate";

/* RN5-0: 110px, wide enough for the widest badge the board carries. */
const BADGE_LANE_CLASS = "w-27.5 shrink-0";

/* RN9-0: 100px, right aligned, tabular so timestamps stack cleanly. */
const META_LANE_CLASS =
  "w-25 shrink-0 truncate text-right text-label text-ink-subtle tabular-nums";

/* The compact control rung: one 28px slot, reserved whether or not it is filled. */
const ACTION_LANE_CLASS = "w-control-sm shrink-0";

/* Hidden until the row is hovered or something inside it takes focus. No transition. */
const ACTION_REVEAL_CLASS =
  "opacity-0 group-hover/queue-row:opacity-100 group-focus-within/queue-row:opacity-100";

type QueueRowDensity = keyof typeof DENSITY_CLASS;

/** Stops a lane control from also activating the row it sits in. */
function stopRowActivation(event: { stopPropagation: () => void }): void {
  event.stopPropagation();
}

interface QueueRowProps {
  /** The thing the row is about: an account, a person, a campaign target. */
  primary: string;
  /** The qualifier that identifies it: channel, owner, stage. Never truncated. */
  secondary?: string;
  /** The reason this row is in the queue. This is what gives way when space runs out. */
  summary?: string;
  /** 40px for scanning volume, 44px when each row carries an identity and a reason. */
  density?: QueueRowDensity;
  /** Weight plus the dot. Never a colour on its own. */
  unread?: boolean;
  /** The fill and `aria-selected`. One row per queue wears it. */
  selected?: boolean;
  /** Activation: click, Enter, or Space. */
  onSelect?: () => void;
  /** Mounts the checkbox lane. Pass it to every row in a queue or to none of them. */
  selectable?: boolean;
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  /** Spoken by the checkbox; name the row, not the act ("Select Bosch"). */
  checkboxLabel?: string;
  /** One `Badge`. The lane is reserved whether or not this row has a state. */
  state?: ReactNode;
  /** The trailing timestamp or count. One lane, fixed width, right aligned. */
  meta?: string;
  /** The one action, an icon `Button`. Revealed on hover and on focus. */
  action?: ReactNode;
}

/** The flexible middle: one line on the data rung, two on the record rung. */
function IdentityCell({
  density,
  primary,
  secondary,
  summary,
  unread,
}: {
  density: QueueRowDensity;
  primary: string;
  secondary?: string;
  summary?: string;
  unread: boolean;
}) {
  const primaryClass = cn(
    PRIMARY_CLASS,
    unread ? PRIMARY_UNREAD_CLASS : PRIMARY_READ_CLASS
  );
  const mutedClass = MUTED_CLASS[density];

  const primaryText = (
    <Box
      data-slot="queue-row-primary"
      render={<span />}
      className={primaryClass}
    >
      {primary}
    </Box>
  );

  const secondaryText =
    secondary === undefined ? null : (
      <Box
        data-slot="queue-row-secondary"
        render={<span />}
        className={cn(mutedClass, SECONDARY_CLASS)}
      >
        {secondary}
      </Box>
    );

  const summaryText =
    summary === undefined ? null : (
      <Box
        data-slot="queue-row-summary"
        render={<span />}
        className={cn(mutedClass, SUMMARY_CLASS)}
      >
        {summary}
      </Box>
    );

  if (density === "data") {
    return (
      <Inline
        data-slot="queue-row-identity"
        gap="sm"
        align="center"
        className={IDENTITY_CLASS}
      >
        {primaryText}
        {secondaryText}
        {summaryText}
      </Inline>
    );
  }

  return (
    <Stack
      data-slot="queue-row-identity"
      gap="none"
      className={cn(IDENTITY_CLASS, IDENTITY_STACK_CLASS)}
    >
      {primaryText}
      {secondaryText === null && summaryText === null ? null : (
        <Inline gap="sm" align="center" className="min-w-0">
          {secondaryText}
          {summaryText}
        </Inline>
      )}
    </Stack>
  );
}

/**
 * One triage row: fixed lanes, two densities, one badge, one hover action, and
 * `aria-selected` on the row itself.
 */
function QueueRow({
  action,
  checkboxLabel,
  checked = false,
  density = "data",
  meta,
  onCheckedChange,
  onSelect,
  primary,
  secondary,
  selectable = false,
  selected = false,
  state,
  summary,
  unread = false,
}: QueueRowProps) {
  /* No hover on a finger: the one action stays put instead of never appearing. */
  const touchPrimary = useTouchPrimary();

  function activate() {
    onSelect?.();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    activate();
  }

  return (
    <Inline
      data-slot="queue-row"
      data-density={density}
      data-selected={selected}
      data-unread={unread}
      render={<li />}
      role="option"
      aria-selected={selected}
      tabIndex={0}
      onClick={activate}
      onKeyDown={handleKeyDown}
      gap="sm"
      align="center"
      className={cn(
        ROW_CLASS,
        DENSITY_CLASS[density],
        selected ? "bg-selected" : undefined
      )}
    >
      {selectable ? (
        <Inline
          data-slot="queue-row-select-lane"
          align="center"
          className={SELECT_LANE_CLASS}
        >
          <Checkbox
            checked={checked}
            onCheckedChange={onCheckedChange}
            onClick={stopRowActivation}
            aria-label={checkboxLabel}
          />
        </Inline>
      ) : null}

      <Inline
        data-slot="queue-row-state-lane"
        align="center"
        className={STATE_LANE_CLASS}
      >
        {unread ? (
          <Box data-slot="queue-row-unread-dot" className={STATE_DOT_CLASS} />
        ) : null}
      </Inline>

      <IdentityCell
        density={density}
        primary={primary}
        secondary={secondary}
        summary={summary}
        unread={unread}
      />

      <Inline
        data-slot="queue-row-badge-lane"
        align="center"
        className={BADGE_LANE_CLASS}
      >
        {state}
      </Inline>

      <Box
        data-slot="queue-row-meta-lane"
        render={<span />}
        className={META_LANE_CLASS}
      >
        {meta}
      </Box>

      <Inline
        data-slot="queue-row-action-lane"
        align="center"
        justify="end"
        onClick={stopRowActivation}
        className={cn(
          ACTION_LANE_CLASS,
          selected || touchPrimary ? undefined : ACTION_REVEAL_CLASS
        )}
      >
        {action}
      </Inline>
    </Inline>
  );
}

interface QueueListProps {
  /** Names the queue. "Alerts", "Inbox", "Campaign exceptions". */
  label: string;
  /** True when the rows carry checkboxes, so more than one row can be selected. */
  multiSelect?: boolean;
  /** `QueueRow` elements, in the order the product ranks them. */
  children: ReactNode;
}

/**
 * The list a `QueueRow` lives in. It exists because `aria-selected` is only
 * legal on a row that an owning role selects: the list is the listbox, the row
 * is the option, and that pair is what makes the selected fill mean something
 * to a screen reader instead of only to an eye.
 */
function QueueList({ children, label, multiSelect = false }: QueueListProps) {
  return (
    <Stack
      data-slot="queue-list"
      render={<ul />}
      role="listbox"
      aria-label={label}
      aria-multiselectable={multiSelect}
      gap="none"
      className="w-full"
    >
      {children}
    </Stack>
  );
}

export { QueueList, QueueRow, QUEUE_ROW_RULES };
export type { QueueListProps, QueueRowDensity, QueueRowProps };
