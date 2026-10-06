"use client";

/*
 * Date picker, on CORE 14.
 *
 * COMPOSITION. An input-shaped trigger, `ui/popover` for the surface, and
 * `ui/calendar` for the grid. The trigger class is imported from `ui/combobox`
 * rather than restated: a picker that opens a panel is one object in this
 * system whatever the panel contains, and two copies of a 40-class string is
 * how two pickers end up 2px apart.
 *
 * FORMATTING IS A DECISION, NOT A LOCALE. `MMM d, yyyy` through date-fns, one
 * constant, every surface. `toLocaleDateString` would render a different string
 * per visitor, which means a screenshot, a VRT baseline and a support ticket
 * all disagree about what the product says; and a numeric format renders
 * `03/04` ambiguously on either side of the Atlantic. A month abbreviation
 * cannot be misread.
 *
 * TWO MODES, ONE COMPONENT. `single` and `range` are a discriminated union
 * rather than two components, because they are the same control answering
 * "which day" versus "which days" and a surface swapping between them should
 * not swap imports. react-day-picker types its own props by mode, so the two
 * branches are written out at the call to `Calendar` instead of being forwarded
 * through a widened prop -- a cast there would be the only place a wrong mode
 * could reach the grid unnoticed.
 *
 * The popup closes when the answer is complete: on the day in single mode, on
 * the second day in range mode. It stays open after the first day of a range,
 * because a half-open range is a question still being asked.
 */

import * as React from "react";
import { format } from "date-fns";
import type { DateRange } from "react-day-picker";

import { CalendarDays } from "./glyphs";
import { Calendar } from "./calendar";
import { cn } from "./cn";
import { FIELD_TRIGGER_CLASS } from "./combobox";
import { Icon } from "./icon";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "./popover";

/** The one display format in the product. See the header note. */
const DATE_DISPLAY_FORMAT = "MMM d, yyyy";

/** En dash, the typographic range mark; a hyphen reads as a minus sign here. */
const DATE_RANGE_SEPARATOR = " – ";

/* A month grid is 7 x 28px plus its padding; the popup sizes to it, not to the trigger. */
const CONTENT_CLASS = "w-auto overflow-y-hidden";

type DatePickerMode = "single" | "range";

interface DatePickerBaseProps {
  /** Shown, quietly, while nothing is picked. */
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  /** Controlled popup state. Omit and the picker owns it. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  "aria-label"?: string;
  "aria-required"?: boolean;
}

interface SingleDatePickerProps extends DatePickerBaseProps {
  mode?: "single";
  value?: Date;
  onValueChange?: (value: Date | undefined) => void;
}

interface RangeDatePickerProps extends DatePickerBaseProps {
  mode: "range";
  value?: DateRange;
  onValueChange?: (value: DateRange | undefined) => void;
}

type DatePickerProps = SingleDatePickerProps | RangeDatePickerProps;

/** The trigger label for one day, or null when there is nothing to say. */
function formatSingleValue(value: Date | undefined): string | null {
  return value === undefined ? null : format(value, DATE_DISPLAY_FORMAT);
}

/** The trigger label for a range. A half-picked range shows only its start. */
function formatRangeValue(value: DateRange | undefined): string | null {
  if (value?.from === undefined) return null;
  const from = format(value.from, DATE_DISPLAY_FORMAT);
  if (value.to === undefined) return from;
  return `${from}${DATE_RANGE_SEPARATOR}${format(value.to, DATE_DISPLAY_FORMAT)}`;
}

/** An input-shaped trigger that opens a month grid. */
function DatePicker(props: DatePickerProps) {
  const {
    className,
    disabled = false,
    onOpenChange,
    open,
    placeholder = "Pick a date",
  } = props;

  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(false);
  const isControlled = open !== undefined;

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) {
        setUncontrolledOpen(next);
      }
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange]
  );

  const label =
    props.mode === "range"
      ? formatRangeValue(props.value)
      : formatSingleValue(props.value);

  function handleSingleSelect(next: Date | undefined): void {
    if (props.mode === "range") return;
    props.onValueChange?.(next);
    if (next !== undefined) {
      setOpen(false);
    }
  }

  function handleRangeSelect(next: DateRange | undefined): void {
    if (props.mode !== "range") return;
    props.onValueChange?.(next);
    if (next?.from !== undefined && next.to !== undefined) {
      setOpen(false);
    }
  }

  return (
    <Popover open={open ?? uncontrolledOpen} onOpenChange={setOpen}>
      <PopoverTrigger
        data-slot="date-picker-trigger"
        disabled={disabled}
        id={props.id}
        aria-describedby={props["aria-describedby"]}
        aria-invalid={props["aria-invalid"]}
        aria-label={props["aria-label"]}
        aria-required={props["aria-required"]}
        className={cn(FIELD_TRIGGER_CLASS, className)}
      >
        <span
          data-slot="date-picker-value"
          className={cn("truncate text-left", label ? null : "text-ink-subtle")}
        >
          {label ?? placeholder}
        </span>
        <Icon icon={CalendarDays} size="sm" className="text-ink-subtle" />
      </PopoverTrigger>
      <PopoverContent
        data-slot="date-picker-content"
        align="start"
        inset="flush"
        className={CONTENT_CLASS}
      >
        {props.mode === "range" ? (
          <Calendar
            mode="range"
            selected={props.value}
            onSelect={handleRangeSelect}
            defaultMonth={props.value?.from}
            numberOfMonths={2}
          />
        ) : (
          <Calendar
            mode="single"
            selected={props.value}
            onSelect={handleSingleSelect}
            defaultMonth={props.value}
          />
        )}
      </PopoverContent>
    </Popover>
  );
}

export {
  DatePicker,
  DATE_DISPLAY_FORMAT,
  DATE_RANGE_SEPARATOR,
  formatRangeValue,
  formatSingleValue,
};
export type {
  DatePickerMode,
  DatePickerProps,
  DateRange,
  RangeDatePickerProps,
  SingleDatePickerProps,
};
