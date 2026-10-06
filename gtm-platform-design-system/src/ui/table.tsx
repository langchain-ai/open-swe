/*
 * Table, on CORE 14.
 *
 * The only primitive in the set with no behaviour library behind it: a table is
 * semantic HTML, and the grid engines (TanStack) drive it from the outside.
 * What this file owns is geometry. Rows are the 40px data step, header cells
 * carry the label size in `ink-subtle` so the head reads as chrome rather than
 * content, and the hairline between rows is `line`, the lighter weight, because
 * `line-strong` belongs to the edge of a control.
 *
 * Flat on canvas by default: no fill, no radius, no border around the table
 * itself. Containment follows commitment, and a table is the content of a
 * panel, not a panel of its own.
 *
 * SCROLLBARS. Wiki 02 lets tables and grids keep a persistent bar, "because a
 * persistent bar is a legitimate affordance on wide data". That is an argument
 * about REVEAL, and it used to be implemented as an argument about which
 * scrollbar: `always` kept a raw `overflow-x-auto` box, which is the platform's
 * bar, its track and its gutter -- the one shape the law forbids everywhere
 * else, readmitted through the only door that was left open. Both settings are
 * one ScrollArea now. `hover` waits for the pointer, `always` rests the same
 * overlay thumb at full opacity, and neither draws a track. The affordance
 * survives; the container it used to arrive in does not.
 *
 * `always` is still not `keepMounted`: a table narrow enough to fit shows no bar
 * on either setting, because "persistent" describes a bar that has something to
 * scroll, not a rail waiting for content.
 *
 * HOVER DOES NOT ANIMATE. The row fill flips instantly, which settles a
 * contradiction this file used to carry: it transitioned the hover tint at
 * `duration-fast` while `QUEUE_ROW_RULES` banned exactly that for lists ("a
 * 160ms tint smears when a pointer sweeps a two-hundred-row queue"). Two of our
 * own files cannot disagree about one interaction, and the queue row is the one
 * that argued its case, so the table follows it. A row is still a pressable
 * surface; it just does not have an opinion about how long it takes to notice
 * the pointer. Header rows are chrome, not pressable: the hover wash is
 * suppressed inside `data-slot=table-header`.
 *
 * HAIRLINES YIELD TO THE FILL. A tinted band sliced by two hairlines reads as
 * three objects, so the hovered row and the row directly above it both drop
 * their bottom border while the pointer is on them. `:has(+ tr:hover)` is what
 * reaches the row above; it is paint-only, costs no layout, and needs no JS.
 *
 * THE LAST HAIRLINE BELONGS TO THE CONTAINER. A table is the content of a
 * panel, and the panel already owns its bottom edge, so the last row of the
 * last section drops its border rather than drawing a second line 1px inside
 * the frame. The suppression is scoped to the body's own last-child position:
 * a body followed by a `<tfoot>` keeps its seam, because there the hairline is
 * separating two sections rather than doubling a frame.
 */

import * as React from "react";

import { cn } from "./cn";
import {
  ScrollArea,
  type ScrollbarReveal,
} from "./scroll-area";

/** Reveal only: `hover` waits for the pointer, `always` rests visible. */
type TableScrollbars = ScrollbarReveal;

interface TableProps extends React.ComponentProps<"table"> {
  /** False hands overflow back to the consumer; see the note at the container. */
  container?: boolean;
  scrollbars?: TableScrollbars;
}

function Table({
  className,
  container = true,
  scrollbars = "hover",
  ...props
}: TableProps) {
  const table = (
    <table
      data-slot="table"
      className={cn("w-full caption-bottom text-body", className)}
      {...props}
    />
  );
  if (!container) {
    // The wrapper is the nearest scroll container, which breaks a consumer's
    // own sticky-header scroll region; such consumers opt out and own overflow.
    return table;
  }
  return (
    <ScrollArea
      data-slot="table-container"
      data-scrollbars={scrollbars}
      scrollbars={scrollbars}
      className="w-full"
    >
      {table}
    </ScrollArea>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b [&_tr]:border-line", className)}
      {...props}
    />
  );
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&:last-child>tr:last-child]:border-b-0", className)}
      {...props}
    />
  );
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t border-line bg-muted font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  );
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "h-row-data border-b border-line hover:border-transparent hover:bg-hover data-[state=selected]:bg-selected [&:has(+tr:hover)]:border-transparent [[data-slot=table-header]_&]:hover:border-line [[data-slot=table-header]_&]:hover:bg-transparent",
        className
      )}
      {...props}
    />
  );
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "px-2 text-left align-middle text-label font-medium whitespace-nowrap text-ink-subtle [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-2 align-middle whitespace-nowrap text-ink [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  );
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-label text-ink-subtle", className)}
      {...props}
    />
  );
}

export type { TableProps, TableScrollbars };
export {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
};
