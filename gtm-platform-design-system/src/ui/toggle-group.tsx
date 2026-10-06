"use client";

/*
 * Toggle group, on CORE 14. This is the segmented control base.
 *
 * THIS IS THE FUTURE POSTURE SWITCH'S BASE. The Paper survey's PostureControl
 * entry (docs/plan/gtm-agent-product/paper-survey-2026-08-05.md) finds the same
 * segmented switch drawn on every single surface board -- the Home/Agent pair
 * in the sidebar rail -- and measures it precisely: a 32px track at radius 10
 * with 2px of padding on a `selected` fill, holding 28px segments at radius 8,
 * where the active segment is a `panel` fill plus a `line-strong` hairline plus
 * a 1px/2px shadow. That is exactly the geometry below, so when the posture
 * control is finally settled (the survey has it at `needs-designer-decision`:
 * it contradicts APP_SHELL_RULES, which says keep the sidebar row and drop the
 * button) it is a composition over this primitive, never a second segmented
 * control. Whoever builds it inherits this geometry rather than re-measuring
 * the boards.
 *
 * GEOMETRY. Track min 32 / radius 10 / `p-0.5`; segments 28 / radius 8. A
 * wrapping caller grows the track with its rows. The 2px
 * padding is what makes the inner corner concentric with the outer, and it is
 * why the two rungs have to be adjacent steps of the density ladder rather than
 * any pair of heights that happen to fit.
 *
 * SELECTED. `bg-panel` + `border-line-strong`, and no shadow (Amal 2026-08-06).
 * The Paper survey measured a 1px/2px drop on the active segment; that reading
 * stacked with the hairline into the same "thicker than fluid" complaint that
 * hit Tabs, so the drop is retired and the hairline keeps the edge. A segment
 * already framed by its track does not also need the raised-pressable rung --
 * that whisper is for buttons that sit alone on a substrate. Unselected
 * segments carry a transparent border so selecting one moves no pixels. The
 * fill pairing separates in both themes, which is the whole reason the boards'
 * raw fills become tokens here.
 *
 * WIDTH. The track is `w-fit` and the segments size to their content. The
 * boards' 50/50 sidebar instance is a placement decision, not the control's
 * nature: that caller passes `flex-1` on its items. A segmented control in a
 * toolbar should not stretch because one in a rail does.
 *
 * MOTION. Color and border cross-fade at 160ms quint; nothing moves.
 * A segmented control is a high-frequency view switch, so it gets no press
 * scale and no sliding indicator -- a thumb that animates between segments is
 * a third of a second of the user waiting to see which view they are on.
 */

import { Toggle as TogglePrimitive } from "@base-ui/react/toggle";
import { ToggleGroup as ToggleGroupPrimitive } from "@base-ui/react/toggle-group";

import { cn } from "./cn";

function ToggleGroup<Value extends string>({
  className,
  ...props
}: ToggleGroupPrimitive.Props<Value>) {
  return (
    <ToggleGroupPrimitive
      data-slot="toggle-group"
      className={cn(
        "inline-flex min-h-control w-fit items-center gap-0.5 rounded-control bg-selected p-0.5",
        className
      )}
      {...props}
    />
  );
}

function ToggleGroupItem<Value extends string>({
  className,
  ...props
}: TogglePrimitive.Props<Value>) {
  return (
    <TogglePrimitive
      data-slot="toggle-group-item"
      className={cn(
        "inline-flex h-control-sm shrink-0 items-center justify-center gap-1.5 rounded-compact border border-transparent px-2.5 text-label font-medium whitespace-nowrap text-ink-subtle transition-[color,background-color,border-color] duration-fast ease-out-quint outline-none select-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary data-disabled:pointer-events-none data-disabled:opacity-50 data-pressed:border-line-strong data-pressed:bg-panel data-pressed:text-ink motion-reduce:transition-none [&_svg]:pointer-events-none [&_svg]:shrink-0",
        className
      )}
      {...props}
    />
  );
}

export { ToggleGroup, ToggleGroupItem };
