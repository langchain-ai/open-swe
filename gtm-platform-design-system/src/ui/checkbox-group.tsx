"use client";

/*
 * Checkbox group, on CORE 14. The fluid-functionalism item (ANALYSIS.md row 21)
 * adapted onto Base UI's `CheckboxGroup` -- the Base flavour, not the Radix
 * one, because our stack is Base UI and the two files differ in exactly the
 * part that matters.
 *
 * WHY IT EXISTS. We shipped a `Checkbox` and a `RadioGroup` and nothing in
 * between, so the multi-select every filter panel and every "which channels"
 * setting needs was a bespoke composition each time -- three surfaces, three
 * row heights, three answers to where the select-all goes. This is the one
 * answer.
 *
 * THE ROW IS A ROW, ON THE DATA RUNG. The reference item sets its items on a
 * 28px compact step; ours is floored at `min-h-row-data` (40), because 28 is
 * the *control* rung and this is not a control, it is a label with a mark in
 * front of it. Placement picks the size (wiki 02 principle 5) and the thing
 * being placed here is a line of text a rep reads down a list of, which is the
 * data rung's job. The mark itself stays 16px on `rounded-tick`: `Checkbox`
 * already decided that a 16px square at half its own size becomes a radio, and
 * a group of them does not get to re-decide it.
 *
 * THE LABEL WRAPS; IT IS THE ONE LANE THAT MAY NOT TRUNCATE. DESIGN.md has
 * every flexible single-line lane truncate, and that rule assumes the full
 * text is one click away. An option label is the thing being chosen, often a
 * verbatim sentence (the POV criteria matrix runs to 160 characters), and a
 * truncated one asks the rep to tick what they cannot read. So the row is a
 * floor rather than a height, the text takes `text-pretty` and wraps, and the
 * mark aligns to the first line: 12px of block padding on the 16px label line
 * lands a one-line row exactly on the 40px rung, and the 16px mark shares
 * that line box.
 *
 * THE ROW CONTAINS ITS HIDDEN INPUT. Base UI renders the real checkbox as an
 * absolutely positioned, clipped input. Without `relative` here its containing
 * block is whatever positioned ancestor comes next, and inside a scroll region
 * that ancestor sits outside the scrollport, so every row's input escaped the
 * clip and added blank scroll height below the list.
 *
 * THE WHOLE ROW IS THE TARGET. It is a real `<label>` wrapping the checkbox, so
 * the text is not a caption beside a hit area, it is part of the hit area. The
 * hover fill flips instantly, matching `QUEUE_ROW_RULES`: a list whose rows
 * fade under a sweeping pointer smears.
 *
 * THE PARENT IS COMPUTED HERE, NOT DELEGATED. Base UI can own an indeterminate
 * parent through `allValues` + `parent`, and we deliberately do not use it: its
 * parent's indeterminate state lives inside the primitive, while our
 * `Checkbox` picks its glyph from an `indeterminate` prop, so the delegated
 * version would draw a tick where it means a dash. Instead this file mirrors
 * the group's value (controlled or not) and derives the parent's three states
 * itself. `allValues` is intercepted rather than forwarded for the same reason.
 * The event details a caller receives are still Base UI's own, handed straight
 * across from the checkbox that produced them -- nothing here fabricates one.
 *
 * WHAT WAS LEFT BEHIND: the proximity hover and the contiguous-selection
 * merging, on the standing rulings in ANALYSIS.md techniques 1 and 12.
 */

import { CheckboxGroup as CheckboxGroupPrimitive } from "@base-ui/react/checkbox-group";
import { createContext, useContext, useId, useState } from "react";
import type { ReactNode } from "react";

import { Checkbox } from "./checkbox";
import { cn } from "./cn";
import { HELP_CLASS, LABEL_CLASS } from "./label";

/* Both parts speak the same `none`-reason detail object; see the header. */
type ChangeDetails = CheckboxGroupPrimitive.ChangeEventDetails;

interface CheckboxGroupContextValue {
  value: readonly string[];
  allValues: readonly string[];
  emit: (next: string[], details: ChangeDetails) => void;
}

const CheckboxGroupContext = createContext<CheckboxGroupContextValue | null>(
  null
);

function useCheckboxGroupContext(): CheckboxGroupContextValue {
  const context = useContext(CheckboxGroupContext);

  if (context === null) {
    throw new Error(
      "CheckboxGroupItem and CheckboxGroupSelectAll must be used inside a CheckboxGroup."
    );
  }

  return context;
}

const GROUP_CLASS = "flex w-full flex-col";

const GROUP_LABEL_CLASS = LABEL_CLASS;
const GROUP_DESCRIPTION_CLASS = cn(HELP_CLASS, "text-pretty");

/*
 * The row: the data rung, the mark's lane, and an instant hover fill. The
 * negative inset is what lets the fill reach past the text without the group
 * itself carrying padding it would then have to subtract from its label.
 */
const ROW_CLASS =
  "relative -mx-2 flex min-h-row-data w-full min-w-0 cursor-pointer items-start gap-2 rounded-compact px-2 py-3 text-label text-ink select-none hover:bg-hover";

/* A disabled row is dimmed and inert as one object, mark and label together. */
const ROW_DISABLED_CLASS = "pointer-events-none opacity-50";

const ROW_TEXT_CLASS = "min-w-0 flex-1 text-pretty break-words";

interface CheckboxGroupProps
  extends Omit<CheckboxGroupPrimitive.Props, "allValues" | "children"> {
  /** Names the group above its rows. */
  label?: string;
  /** One line under the label, measure-capped. */
  description?: string;
  /** Every item value, in order. Only a select-all row needs it. */
  allValues?: readonly string[];
  children: ReactNode;
}

/**
 * A set of checkboxes that share one value. Controlled through `value` /
 * `onValueChange`, or uncontrolled through `defaultValue`.
 */
function CheckboxGroup({
  allValues = [],
  children,
  className,
  defaultValue,
  description,
  label,
  onValueChange,
  value,
  ...props
}: CheckboxGroupProps) {
  const labelId = useId();
  const descriptionId = useId();
  const [internal, setInternal] = useState<readonly string[]>(
    defaultValue ?? []
  );
  const current = value ?? internal;

  function emit(next: string[], details: ChangeDetails) {
    if (value === undefined) {
      setInternal(next);
    }

    onValueChange?.(next, details);
  }

  return (
    <CheckboxGroupContext.Provider value={{ allValues, emit, value: current }}>
      <CheckboxGroupPrimitive
        data-slot="checkbox-group"
        value={[...current]}
        onValueChange={emit}
        aria-labelledby={label === undefined ? undefined : labelId}
        aria-describedby={description === undefined ? undefined : descriptionId}
        className={cn(GROUP_CLASS, className)}
        {...props}
      >
        {label === undefined ? null : (
          <span id={labelId} className={GROUP_LABEL_CLASS}>
            {label}
          </span>
        )}
        {description === undefined ? null : (
          <span id={descriptionId} className={GROUP_DESCRIPTION_CLASS}>
            {description}
          </span>
        )}
        {children}
      </CheckboxGroupPrimitive>
    </CheckboxGroupContext.Provider>
  );
}

interface CheckboxGroupItemProps {
  /** The value this row contributes to the group. */
  value: string;
  /** Submitted with a form; defaults to the value. */
  name?: string;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}

/** One row: the mark, then the label, and the whole row is the target. */
function CheckboxGroupItem({
  children,
  className,
  disabled = false,
  name,
  value,
}: CheckboxGroupItemProps) {
  return (
    <label
      data-slot="checkbox-group-item"
      className={cn(ROW_CLASS, disabled ? ROW_DISABLED_CLASS : undefined, className)}
    >
      <Checkbox value={value} name={name ?? value} disabled={disabled} />
      <span className={ROW_TEXT_CLASS}>{children}</span>
    </label>
  );
}

interface CheckboxGroupSelectAllProps {
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * The parent row. Checked when every value is on, indeterminate when some are,
 * and a click sets the group to all or to none.
 */
function CheckboxGroupSelectAll({
  children,
  className,
  disabled = false,
}: CheckboxGroupSelectAllProps) {
  const { allValues, emit, value } = useCheckboxGroupContext();
  const selected = allValues.filter((item) => value.includes(item)).length;
  const all = allValues.length > 0 && selected === allValues.length;
  const some = selected > 0 && !all;

  return (
    <label
      data-slot="checkbox-group-select-all"
      className={cn(ROW_CLASS, disabled ? ROW_DISABLED_CLASS : undefined, className)}
    >
      <Checkbox
        checked={all}
        indeterminate={some}
        disabled={disabled}
        onCheckedChange={(checked, details) => {
          emit(checked ? [...allValues] : [], details);
        }}
      />
      <span className={ROW_TEXT_CLASS}>{children}</span>
    </label>
  );
}

export { CheckboxGroup, CheckboxGroupItem, CheckboxGroupSelectAll };
export type {
  CheckboxGroupItemProps,
  CheckboxGroupProps,
  CheckboxGroupSelectAllProps,
};
