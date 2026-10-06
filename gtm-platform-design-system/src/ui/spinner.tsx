/*
 * Spinner, on CORE 14. The one loading glyph in the product.
 *
 * It is `<Icon>` with the barrel's indeterminate-ring glyph (`Loader2`) and
 * `animate-spin`, not a second SVG drawn at the call site. The wrapper already
 * owns the 14/16/20/24 ladder and the drawn set, so a spinner sitting beside a
 * chevron in a toolbar is guaranteed to match it; the data grid's private
 * `DataGridSpinner` was a second hand-rolled circle, which is how a suite ends
 * up one refactor away from two loading glyphs that no longer look alike. It
 * was deleted for this. The ring itself lives in `spinner-glyph.tsx` and enters
 * through the same micro/mini/full triple as every other glyph.
 *
 * MOTION. A spinner keeps spinning under `prefers-reduced-motion`, deliberately,
 * and it is the only thing in this system that does. Reduced motion exists to
 * remove decoration and vestibular triggers; a small steady rotation is neither,
 * and stopping it turns "still working" into "frozen", which is the one message
 * a loading state must never send. Emil's round set the precedent for that
 * asymmetry: motion that carries state survives the media query, motion that
 * carries delight does not. Everything else in `ui/` still stops.
 *
 * VOICE. Decorative by default, because both of its call sites stand next to the
 * word "Loading" and a screen reader does not need to hear it twice. A spinner
 * that is genuinely alone passes `label`, and `<Icon>` turns it into a named
 * image.
 *
 * It carries `<Icon>`'s own `data-slot="icon"` rather than a slot of its own,
 * because delegating is the point: the day this needs `data-slot="spinner"` is
 * the day `<Icon>` learns to forward attributes, not the day this file starts
 * drawing its own SVG again.
 *
 * WHY THE STATUS LABEL LIVES HERE. A loading glyph almost never stands alone;
 * it stands beside a word that says what is loading, and that word changes as
 * the work does. The glyph and its word are one decision, so they are one file:
 * `StatusLabel` is the second half of `Spinner`, and `ui/orb.tsx` reaches for
 * the same half rather than inventing a second one.
 */


import type { ComponentProps } from "react";

import { Loader2 } from "./glyphs";
import { cn } from "./cn";
import { Icon, type IconSize } from "./icon";

/*
 * THE GHOST (ANALYSIS.md row 44).
 *
 * A status word that changes in place -- "working..." to "completed", "Loading"
 * to "Loaded" -- resizes its own row every time it changes, and everything after
 * it on that line jumps. The fix is not a fixed width, which is a measurement
 * nobody can maintain: it is to render every word the row may ever show, stacked
 * in ONE `inline-grid` cell, with all but the current one at `visibility:hidden`.
 * The cell measures to the widest of them, so the row is already the size of its
 * worst case before the first change lands, and the visible word swaps inside a
 * box that never moves.
 *
 * `invisible` rather than `opacity-0` is load-bearing: `visibility:hidden` keeps
 * the layout contribution, which is the entire point, while removing the ghost
 * from paint, from hit-testing, AND from the accessibility tree. The ghosts also
 * carry `aria-hidden`, so a `StatusLabel` sitting inside a live region (which is
 * exactly where these end up) announces one word rather than the union of them.
 *
 * WHAT ROW 44 REJECTED STAYS REJECTED. No cycling word list -- "Moonwalking" in
 * a seller's inbox -- and no morphing SVG. `label` is driven by real state, never
 * by a timer, and this component owns no timer for that reason. Which is also the
 * reduced-motion answer: the only motion a status label has ever carried here is
 * the in-flight pulse, and `pending` owns it along with the `motion-reduce` stop,
 * in one place, so that a call site can no longer ship the pulse and forget the
 * media query. Under reduced motion the label is simply the static word.
 */

/** All labels in one cell, so the widest of them sizes the row once and for all. */
const STATUS_LABEL_CLASS = "inline-grid";

/** Every candidate occupies the same cell; the grid takes the widest. */
const STATUS_LABEL_CELL_CLASS = "col-start-1 row-start-1";

/** In flight: the label breathes, and stops dead under reduced motion. */
const STATUS_LABEL_PENDING_CLASS = "animate-pulse motion-reduce:animate-none";

interface StatusLabelProps extends Omit<ComponentProps<"span">, "children"> {
  /** The word showing now. Driven by state, never by a timer. */
  label: string;
  /**
   * Every word this row may show. The row reserves the widest of them, so a
   * change of state never moves anything after it on the line.
   */
  reserve?: readonly string[];
  /** True while the work behind the label is in flight. */
  pending?: boolean;
}

/** A status word that swaps in place without resizing its row. */
function StatusLabel({
  label,
  reserve,
  pending = false,
  className,
  ...props
}: StatusLabelProps) {
  const ghosts = reserve?.filter((candidate) => candidate !== label) ?? [];

  return (
    <span
      data-slot="status-label"
      className={cn(STATUS_LABEL_CLASS, className)}
      {...props}
    >
      {ghosts.map((ghost) => (
        <span
          key={ghost}
          aria-hidden
          className={cn(STATUS_LABEL_CELL_CLASS, "invisible")}
        >
          {ghost}
        </span>
      ))}
      <span
        data-slot="status-label-current"
        className={cn(
          STATUS_LABEL_CELL_CLASS,
          pending && STATUS_LABEL_PENDING_CLASS
        )}
      >
        {label}
      </span>
    </span>
  );
}

interface SpinnerProps {
  /** Ladder slot: 14 / 16 / 20 / 24. */
  size?: IconSize;
  /** Set only when the spinner is the only sign that work is running. */
  label?: string;
  /** Colour only (`text-ink-subtle`); geometry comes from `size`. */
  className?: string;
}

function Spinner({ size = "md", label, className }: SpinnerProps) {
  return (
    <Icon
      icon={Loader2}
      size={size}
      label={label}
      className={cn("animate-spin", className)}
    />
  );
}

export { Spinner, StatusLabel };
export type { SpinnerProps, StatusLabelProps };
