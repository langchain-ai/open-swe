"use client";

/*
 * Timeline, retokenized from ReUI's Base UI timeline
 * (https://reui.io/docs/components/base/timeline, registry `timeline`).
 *
 * WHAT IT IS. A rail of events: a vertical (or horizontal) separator, a node
 * on that line, and content beside it. The gutter is tight (`ms-6`) so the
 * rail sits next to the card, not in a vacant lane. The node is a 10px disc
 * with a shell ring so the line threads behind it, a hairline tick from the
 * disc to the card, and `h-full` on the separator so the rail meets the next
 * node instead of dying at the item edge. Tokens are ours: `bg-line-strong`
 * for the rail, `text-meta` for the date, no `muted-foreground` / `primary/10`.
 * A hop from ReUI still lands on the same slots; the geometry is tighter.
 *
 * NOT A STEPPER. Upstream tracks `activeStep` so a pipeline can paint
 * completed nodes. Mail and activity feeds are a record, not a progress bar,
 * so this port does not own that state. A caller that needs a live step
 * colours the indicator itself.
 *
 * MOTION. None. The line is structure.
 */

import type { ComponentProps } from "react";

import { cn } from "./cn";
import { HELP_CLASS, LABEL_CLASS } from "./label";

function Timeline({
  className,
  orientation = "vertical",
  ...props
}: ComponentProps<"div"> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      data-slot="timeline"
      data-orientation={orientation}
      className={cn(
        "group/timeline flex",
        orientation === "horizontal" ? "w-full flex-row" : "flex-col",
        className
      )}
      {...props}
    />
  );
}

function TimelineItem({
  className,
  step,
  ...props
}: ComponentProps<"div"> & { step: number }) {
  return (
    <div
      data-slot="timeline-item"
      data-step={step}
      className={cn(
        "group/timeline-item relative flex flex-1 flex-col",
        "group-data-[orientation=vertical]/timeline:ms-6",
        "group-data-[orientation=vertical]/timeline:not-last:pb-6",
        "group-data-[orientation=horizontal]/timeline:mt-6",
        "group-data-[orientation=horizontal]/timeline:not-last:pe-6",
        className
      )}
      {...props}
    />
  );
}

function TimelineSeparator({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      aria-hidden
      data-slot="timeline-separator"
      className={cn(
        "pointer-events-none absolute z-0 self-start bg-line-strong",
        "group-last/timeline-item:hidden",
        "group-data-[orientation=vertical]/timeline:-left-4",
        "group-data-[orientation=vertical]/timeline:top-5",
        "group-data-[orientation=vertical]/timeline:h-full",
        "group-data-[orientation=vertical]/timeline:w-px",
        "group-data-[orientation=vertical]/timeline:-translate-x-1/2",
        "group-data-[orientation=horizontal]/timeline:-top-4",
        "group-data-[orientation=horizontal]/timeline:left-5",
        "group-data-[orientation=horizontal]/timeline:h-px",
        "group-data-[orientation=horizontal]/timeline:w-full",
        "group-data-[orientation=horizontal]/timeline:-translate-y-1/2",
        className
      )}
      {...props}
    />
  );
}

function TimelineIndicator({
  className,
  children,
  connector = true,
  ...props
}: ComponentProps<"div"> & {
  /** The hairline from the node to a card beside it. Off for rows of plain text, which it would strike through. */
  connector?: boolean;
}) {
  return (
    <div
      aria-hidden
      data-slot="timeline-indicator"
      className={cn(
        "absolute isolate z-10 size-2.5 rounded-full bg-line-strong ring-2 ring-shell",
        "group-data-[orientation=vertical]/timeline:-left-4",
        "group-data-[orientation=vertical]/timeline:top-5",
        "group-data-[orientation=vertical]/timeline:-translate-x-1/2",
        "group-data-[orientation=horizontal]/timeline:-top-4",
        "group-data-[orientation=horizontal]/timeline:left-5",
        "group-data-[orientation=horizontal]/timeline:-translate-y-1/2",
        className
      )}
      {...props}
    >
      {connector ? (
        <span
          aria-hidden
          data-slot="timeline-connector"
          className="pointer-events-none absolute top-1/2 left-full z-[-1] hidden h-px w-4 -translate-y-1/2 bg-line-strong group-data-[orientation=vertical]/timeline:block"
        />
      ) : null}
      {children}
    </div>
  );
}

function TimelineHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div data-slot="timeline-header" className={cn(className)} {...props} />
  );
}

function TimelineDate({ className, ...props }: ComponentProps<"time">) {
  return (
    <time
      data-slot="timeline-date"
      className={cn(HELP_CLASS, className)}
      {...props}
    />
  );
}

function TimelineTitle({ className, ...props }: ComponentProps<"h3">) {
  return (
    <h3
      data-slot="timeline-title"
      className={cn(LABEL_CLASS, className)}
      {...props}
    />
  );
}

function TimelineContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="timeline-content"
      className={cn("min-w-0", className)}
      {...props}
    />
  );
}

export {
  Timeline,
  TimelineContent,
  TimelineDate,
  TimelineHeader,
  TimelineIndicator,
  TimelineItem,
  TimelineSeparator,
  TimelineTitle,
};
