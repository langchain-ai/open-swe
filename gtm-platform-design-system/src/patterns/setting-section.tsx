"use client";

/*
 * Rules for SettingSection / SettingRow.
 *
 * The rules themselves are `SETTING_RULES` below, not this comment. `/design`
 * renders that array verbatim beside the live pattern, so the anatomy a builder
 * reads in the gallery and the anatomy this file imposes are the same strings.
 *
 * This is the organism tier for the configuration family. What the code
 * enforces: one control per row, the fixed control lane, the container query
 * that stacks the row rather than a viewport breakpoint, the separator the row
 * cancels on itself, and the save-on-change lifecycle with its reserved status
 * lane. What the rules state: that the save semantics are the Rothenberg model
 * adopted pending Amal's confirmation, why a settings page has no Save button,
 * and why the danger zone is PageFrame's slot rather than a row variant.
 *
 * Sections do not re-answer the heading or the containment question:
 * SettingSection composes PageSection, so the commitment ledger stays in one
 * place and a settings page cannot grow a second kind of section heading.
 */

import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";

import { PageSection } from "./page-frame";
import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import { AlertTriangle, Check, Loader2 } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { HELP_CLASS, LABEL_CLASS } from "../ui/label";
import { problemMessage } from "../lib/problem";

const SETTING_RULES: readonly string[] = [
  "A row is one setting: a label, at most a one line description beneath it, and exactly ONE control opposite. Two controls in a lane are two settings that have not been split yet, and a description that needs a second line is a section, not a row.",
  "SAVE SEMANTICS, ADOPTED FROM THE ROTHENBERG MODEL, PENDING AMAL'S CONFIRMATION: settings save on change, per row. There is no Save button, no Cancel button, and no page level dirty bar on a settings surface, because a settings page is a set of independent switches rather than one document with a commit point. The row that changed is the row that saves, and the row that failed is the only row that has to be retried.",
  "Because there is no Save button, the row owes a transient acknowledgement: Saving while the promise is pending, Saved for a moment after it resolves, and the reason inline on the risk role when it rejects. The status lane is reserved at all times, so an acknowledgement never reflows the page, and it never animates. Save on change fires on every toggle, and a high frequency interaction gets no motion.",
  "A rejected save leaves the row exactly as the user set it, states the reason under the control, and marks the control aria-invalid pointing at that reason. It never silently reverts, and it never becomes a toast: the failure belongs beside the setting that failed.",
  "The control sits in a fixed lane, not flexed to the right edge. A page of rows then has one control edge, which is what makes a settings surface read as composed rather than assembled. The lane is a constant here and wants a named step in the geometry ladder; it is a stock width until tokens.css reopens.",
  "The row stacks when ITS CONTAINER is narrow, through a container query, never a viewport breakpoint. A settings page under a 420px agent dock is narrow in its container while the viewport is wide, and a breakpoint gets that case wrong every time.",
  "The row owns its separator and cancels it on the last child in CSS. The parent stays a plain map with no index arithmetic and no `index > 0 && <Separator/>` at the call site, which is how a stray rule above the first row happens.",
  "Badges ride inline with the label, never in the control lane. Required, Beta, Recommended are all statements about the setting, and putting them in the lane breaks the one control edge the lane exists to create.",
  "Identity first, danger last. The rows a user came to read about themselves lead the page; anything destructive leaves this file entirely and arrives through PageFrame's `dangerZone` slot, which pins it to the bottom structurally. A destructive row is not a row variant.",
  "A destructive control inside a row still composes ConfirmableAction. Save on change means one click commits, and one click must never be able to commit something irreversible.",
  "No collapsible sections inside the reading column. Product Settings may swap the app rail for a scoped section jump list; that is chrome, not a second page organisation device inside PageFrame. Collapse still hides the thing the user came to change and is rejected.",
  "Product settings are separate pages under `/settings/*` with compact feature rows per flag (see SETTINGS_PRODUCT_RULES). SettingSection remains the dense mixed-control organism for `/design`. Do not force `headerPlacement=\"in-panel\"` on it.",
  "A boolean setting takes Switch from `components/ui/switch`. A switch states that something is on right now and flips it immediately, which is exactly what save on change does; a checkbox states an intention that a Save button will later act on, and there is no Save button here. Checkbox stays correct for the other question, which is membership: a row that means 'which of these' rather than 'is this on' takes checkboxes. Do not hand roll either at a call site.",
];

/** How long the Saved acknowledgement stays on screen before the lane clears. */
const SAVED_INDICATOR_MS = 2000;

/** Copy shown when a rejected save carries nothing a user could act on. */
const GENERIC_SAVE_FAILURE = "That did not save. The setting is unchanged.";

/*
 * The lane. ReUI settings-7 fixes the control content at 312px (`w-78`) and
 * trailing-aligns it so mixed Input/Select/Switch rows share one edge. Compact
 * boolean rows (product Settings) skip the wide lane so a Switch sits flush on
 * the trailing edge — quieter than the full lane, still save-on-change.
 */
const CONTROL_LANE_CLASS = "w-full shrink-0 @md:w-78";
const CONTROL_LANE_COMPACT_CLASS = "w-auto shrink-0";

/* The label column, capped so a long label wraps rather than eating the lane. */
const LABEL_COLUMN_CLASS = "min-w-0 @md:max-w-sm @md:flex-1";
const LABEL_COLUMN_COMPACT_CLASS = "min-w-0 flex-1";

/*
 * Container query, not a breakpoint: the row is a column until its own
 * container passes the @md step, then it becomes label | lane. Horizontal pad
 * matches settings-7 (`px-5`); the flush card body does not add a second pad.
 */
const ROW_LAYOUT_CLASS =
  "px-5 py-3 @md:flex-row @md:items-center @md:justify-between @md:gap-4";
const ROW_LAYOUT_COMPACT_CLASS =
  "px-5 py-2.5 @md:flex-row @md:items-center @md:justify-between @md:gap-3";

/* The row draws its own hairline and cancels it on itself when it is last. */
const ROW_SEAM_CLASS = "border-b border-line last:border-b-0";

/* Reserved at one meta line so an acknowledgement cannot reflow the page. */
const STATUS_LANE_CLASS = "min-h-4 text-meta";

type SettingSaveState = "IDLE" | "SAVING" | "SAVED" | "FAILED";

/** What a control may hand back to `commit`. */
type SettingValue = string | number | boolean;

function saveFailureMessage(reason: unknown): string {
  if (typeof reason === "string" && reason.length > 0) {
    return problemMessage(new Error(reason), GENERIC_SAVE_FAILURE);
  }
  return problemMessage(reason, GENERIC_SAVE_FAILURE);
}

/** Joins the ids a control should point at, or nothing when it points nowhere. */
function describedBy(ids: readonly (string | null)[]): string | undefined {
  const present = ids.filter((id): id is string => id !== null);
  return present.length === 0 ? undefined : present.join(" ");
}

/**
 * What the row hands its control.
 *
 * A render slot rather than a cloned element, because the callback a control
 * needs is control shaped: Checkbox reports through `onCheckedChange`, Select
 * through `onValueChange`, Input through an event. `commit` is the row's save,
 * and the aria wiring comes back through the same object so the row stays the
 * one place that knows whether the last save failed.
 */
interface SettingControlSlot {
  /** The id the label points at. Put it on the control. */
  id: string;
  /** Save on change: hand the control's new value straight to this. */
  commit: (value: SettingValue) => void;
  /** True while this row's save is in flight; disable the control with it. */
  saving: boolean;
  /** For `aria-describedby`: the description, plus the failure when there is one. */
  describedById: string | undefined;
  /** True when the last save was rejected; feed it to `aria-invalid`. */
  invalid: boolean;
}

interface SettingRowProps {
  label: string;
  /** One line. Two lines means this wanted to be a section. */
  description?: string;
  /** Rides inline with the label. Never in the control lane. */
  badge?: ReactNode;
  /**
   * Compact boolean / select rows (product Settings): Switch sits on the
   * trailing edge without a 288px lane, and the status lane only mounts while
   * a save is in flight or failed — REUI c-switch-13 density.
   */
  density?: "default" | "compact";
  /**
   * Save on change. Resolving shows the transient Saved acknowledgement;
   * rejecting states the reason under the control and marks it invalid.
   * Omit it for a row whose control commits elsewhere (ConfirmableAction).
   */
  onSave?: (value: SettingValue) => Promise<void>;
  /** The row's ONE control, given the row's id, save callback, and aria wiring. */
  control: (slot: SettingControlSlot) => ReactNode;
}

/** One setting: label and description on the left, its single control opposite. */
function SettingRow({
  label,
  description,
  badge,
  density = "default",
  onSave,
  control,
}: SettingRowProps) {
  const controlId = useId();
  const descriptionId = useId();
  const failureId = useId();
  const [state, setState] = useState<SettingSaveState>("IDLE");
  const [failure, setFailure] = useState<string | null>(null);
  const compact = density === "compact";

  /*
   * The acknowledgement's lifetime is derived from the state rather than held
   * in a timer ref: entering SAVED starts the clock, leaving it (a second
   * change, an unmount) cancels it, and nothing has to remember a handle.
   */
  useEffect(() => {
    if (state !== "SAVED") return;
    const timer = setTimeout(() => {
      setState("IDLE");
    }, SAVED_INDICATOR_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [state]);

  function commit(value: SettingValue): void {
    if (onSave === undefined) return;
    setState("SAVING");
    setFailure(null);
    onSave(value)
      .then(() => {
        setState("SAVED");
      })
      .catch((reason: unknown) => {
        setState("FAILED");
        setFailure(saveFailureMessage(reason));
      });
  }

  const slot: SettingControlSlot = {
    commit,
    describedById: describedBy([
      description === undefined ? null : descriptionId,
      failure === null ? null : failureId,
    ]),
    id: controlId,
    invalid: state === "FAILED",
    saving: state === "SAVING",
  };

  const showStatus =
    onSave !== undefined &&
    (!compact || state === "SAVING" || state === "SAVED" || failure !== null);

  return (
    <Stack
      data-slot="setting-row"
      data-state={state}
      data-density={density}
      gap="sm"
      className={cn(
        compact ? ROW_LAYOUT_COMPACT_CLASS : ROW_LAYOUT_CLASS,
        ROW_SEAM_CLASS
      )}
    >
      <Stack gap="xs" className={compact ? LABEL_COLUMN_COMPACT_CLASS : LABEL_COLUMN_CLASS}>
        <Inline gap="sm" align="center" wrap>
          <Box
            render={<label htmlFor={controlId} />}
            className={LABEL_CLASS}
          >
            {label}
          </Box>
          {badge}
        </Inline>
        {description === undefined ? null : (
          <Box
            render={<p id={descriptionId} />}
            className={HELP_CLASS}
          >
            {description}
          </Box>
        )}
      </Stack>

      <Stack gap="xs" className={compact ? CONTROL_LANE_COMPACT_CLASS : CONTROL_LANE_CLASS}>
        <Inline
          gap="sm"
          align="center"
          justify={compact ? "start" : "end"}
          className={cn("min-h-control", compact ? undefined : "w-full")}
        >
          {control(slot)}
        </Inline>
        {showStatus ? (
          <Inline
            role="status"
            aria-live="polite"
            gap="sm"
            align="center"
            className={STATUS_LANE_CLASS}
          >
            {state === "SAVING" ? (
              <Inline gap="sm" align="center" className="text-ink-subtle">
                <Icon icon={Loader2} size="sm" className="animate-spin" />
                <Box render={<span />}>Saving</Box>
              </Inline>
            ) : null}
            {state === "SAVED" ? (
              <Inline gap="sm" align="center" className="text-positive">
                <Icon icon={Check} size="sm" />
                <Box render={<span />}>Saved</Box>
              </Inline>
            ) : null}
            {failure === null ? null : (
              <Inline
                id={failureId}
                role="alert"
                gap="sm"
                align="start"
                className="text-risk"
              >
                <Icon icon={AlertTriangle} size="sm" />
                <Box render={<span />}>{failure}</Box>
              </Inline>
            )}
          </Inline>
        ) : null}
      </Stack>
    </Stack>
  );
}

interface SettingSectionProps {
  title: string;
  /** One or two lines at most; longer belongs in the rows themselves. */
  description?: string;
  /** Section level controls, trailing the heading. */
  actions?: ReactNode;
  /**
   * Forwarded to PageSection's commitment ledger. A settings section is not
   * automatically a panel: the ledger still decides, per surface.
   */
  contained?: boolean;
  /** Anchor id for the settings scoped rail. Forwarded to PageSection. */
  id?: string;
  /** SettingRow children, in the order the ticket lists them. */
  children: ReactNode;
}

/** A group of related settings behind one heading. Rows stack; they never nest. */
function SettingSection({
  title,
  description,
  actions,
  contained = false,
  id,
  children,
}: SettingSectionProps) {
  return (
    <PageSection
      id={id}
      title={title}
      description={description}
      actions={actions}
      contained={contained}
      inset={contained ? "padded" : undefined}
    >
      <Stack data-slot="setting-rows" gap="none" className="@container">
        {children}
      </Stack>
    </PageSection>
  );
}

export { SettingRow, SettingSection, SETTING_RULES };
export type {
  SettingControlSlot,
  SettingRowProps,
  SettingSectionProps,
  SettingValue,
};
