"use client";

/*
 * Calendar, on CORE 14.
 *
 * react-day-picker owns the date maths and the keyboard grid; this file owns
 * every pixel. A day cell is 28px, the compact step, which is what a dense
 * product wants inside a filter popover and what keeps a whole month inside a
 * popover without scrolling. Days reuse the ghost Button so hover, focus ring
 * and press scale are literally the same rules as every other pressable.
 *
 * Selection is the primary pair; the middle of a range is `selected`, the
 * quietest fill that still separates from `panel` in both themes, so the band
 * reads as one continuous run without competing with its own endpoints. Range
 * days square off their corners for the same reason: a rounded edge inside a
 * run breaks the band.
 */

import * as React from "react";
import { DayPicker, type ClassNames } from "react-day-picker";

import { ChevronLeft, ChevronRight } from "./glyphs";
import { buttonVariants } from "./button";
import { cn } from "./cn";
import { Icon } from "./icon";
import { LABEL_CLASS } from "./label";

type CalendarProps = React.ComponentProps<typeof DayPicker>;

function CalendarPreviousIcon() {
  return <Icon icon={ChevronLeft} size="sm" />;
}

function CalendarNextIcon() {
  return <Icon icon={ChevronRight} size="sm" />;
}

const DAY_PICKER_COMPONENTS: CalendarProps["components"] = {
  IconLeft: CalendarPreviousIcon,
  IconRight: CalendarNextIcon,
};

const DAY_PICKER_CLASSES: Partial<ClassNames> = {
  months: "flex flex-col gap-4 sm:flex-row",
  month: "flex flex-col gap-3",
  caption: "relative flex items-center justify-center pt-1",
  caption_label: LABEL_CLASS,
  caption_dropdowns: "flex items-center gap-1",
  dropdown:
    "rounded-compact border border-line-strong bg-panel px-1 py-0.5 text-label text-ink outline-none focus-visible:ring-2 focus-visible:ring-primary",
  dropdown_icon: "text-ink-subtle",
  nav: "flex items-center gap-1",
  nav_button: cn(
    buttonVariants({ variant: "ghost", size: "icon-sm" }),
    "text-ink-subtle hover:text-ink"
  ),
  nav_button_previous: "absolute left-1",
  nav_button_next: "absolute right-1",
  table: "w-full border-collapse",
  head_row: "flex",
  head_cell:
    "w-control-sm text-meta font-normal tracking-caps text-ink-subtle uppercase",
  row: "mt-1 flex w-full",
  cell: "relative h-control-sm w-control-sm p-0 text-center focus-within:relative focus-within:z-20",
  day: cn(
    buttonVariants({ variant: "ghost", size: "icon-sm" }),
    "w-full p-0 text-label font-normal"
  ),
  day_selected: "bg-primary text-primary-ink hover:bg-primary",
  day_today: "bg-hover font-medium text-ink",
  day_outside: "text-ink-subtle opacity-50",
  day_disabled: "text-ink-subtle opacity-50",
  day_range_start: "rounded-r-none",
  day_range_end: "rounded-l-none",
  /*
   * A day in the middle of a range carries `day_selected` too, and two
   * same-specificity colour rules resolve on stylesheet order, which is
   * Tailwind's business and not ours. The important modifier makes the quiet
   * band beat the primary fill deterministically instead of by luck.
   */
  day_range_middle: "rounded-none bg-selected! text-ink!",
  day_hidden: "invisible",
};

function Calendar({
  className,
  classNames,
  components,
  showOutsideDays = true,
  ...props
}: CalendarProps) {
  return (
    <DayPicker
      data-slot="calendar"
      showOutsideDays={showOutsideDays}
      className={cn("w-fit p-3", className)}
      classNames={{ ...DAY_PICKER_CLASSES, ...classNames }}
      components={{ ...DAY_PICKER_COMPONENTS, ...components }}
      {...props}
    />
  );
}

export { Calendar };
export type { CalendarProps };
