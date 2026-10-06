"use client";

/*
 * Progress, on CORE 14.
 *
 * GEOMETRY. A 4px track on `line`, the same hairline colour the product draws
 * its rules in, with a `primary` fill over it. It is a line, not a control: it
 * takes no height from the density ladder, it has no radius from the radius
 * ladder, and `rounded-full` is its cap shape rather than a containment step.
 * There is no size axis, because a progress bar that is taller than a hairline
 * has stopped being chrome and started being a chart.
 *
 * MOTION. The fill is full width and slides under an `overflow-hidden` track on
 * `translateX`, rather than growing on `width`. That is the law ("motion is
 * transform + opacity", never animate width or height) and it is also the only
 * version that stays smooth: width is a layout property and every tick would
 * cost a reflow. 160ms quint on the transform, and `motion-reduce` drops the
 * transition so the bar snaps to its value instead of gliding to it.
 *
 * INDETERMINATE is the same track carrying a softened full-width wash on the
 * sanctioned pulse -- the one Skeleton uses, the only indeterminate loop in the
 * system. It is `primary/40` and not `primary` so that "extent unknown" cannot
 * be misread as "finished": a solid bar means 100, a wash means we do not know.
 * Under `prefers-reduced-motion` the pulse stops and the wash stays, which is
 * the whole requirement -- reduced motion removes the movement, never the state.
 * `aria-valuenow` is omitted in this mode, which is exactly how a screen reader
 * says the same thing.
 */

import { cn } from "./cn";

interface ProgressProps
  extends Omit<React.ComponentProps<"div">, "children" | "role"> {
  /** How far along, from 0 to `max`. Clamped; ignored when indeterminate. */
  value?: number;
  /** The top of the range. Defaults to a percentage. */
  max?: number;
  /** Work is running with no known extent. */
  indeterminate?: boolean;
  /** What is progressing; announced, since a bar has no text of its own. */
  label: string;
}

function Progress({
  className,
  value = 0,
  max = 100,
  indeterminate = false,
  label,
  ...props
}: ProgressProps) {
  const span = max > 0 ? max : 100;
  const clamped = Math.min(Math.max(value, 0), span);
  const percent = (clamped / span) * 100;

  return (
    <div
      data-slot="progress"
      data-indeterminate={indeterminate}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={span}
      aria-valuenow={indeterminate ? undefined : clamped}
      className={cn("h-1 w-full overflow-hidden rounded-full bg-line", className)}
      {...props}
    >
      {indeterminate ? (
        <div
          data-slot="progress-indicator"
          className="size-full animate-pulse rounded-full bg-primary/40 motion-reduce:animate-none"
        />
      ) : (
        <div
          data-slot="progress-indicator"
          style={{ transform: `translateX(-${100 - percent}%)` }}
          className="size-full rounded-full bg-primary transition-transform duration-fast ease-out-quint motion-reduce:transition-none"
        />
      )}
    </div>
  );
}

export { Progress };
export type { ProgressProps };
