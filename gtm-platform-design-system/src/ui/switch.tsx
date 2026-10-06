"use client";

/*
 * Switch, on CORE 14.
 *
 * GEOMETRY. The Paper boards fix the switch exactly: a 24x14 track carrying a
 * 10px thumb. Those measures want to be `--width-gtm-switch` /
 * `--height-gtm-switch`, and tokens.css carries neither, so the track is
 * written as the literal ladder-legal utilities `w-6 h-3.5` and the thumb as
 * `size-2.5`. They are stock steps, not arbitrary values, and they become named
 * tokens the moment tokens.css reopens. Like Checkbox, a switch is not a rung
 * of the density ladder: it sits inside a 28px row or beside a 32px control and
 * has to stay smaller than both.
 *
 * ROUNDING. Both track and thumb are `rounded-full`, on the same terms as the
 * radio in radio-group.tsx: a switch is a capsule with a disc riding in it, and
 * the roundness is the shape's meaning rather than a radius picked off the
 * ladder. A 6px corner on a 14px track is not a smaller switch, it is a
 * different object. This is a stated exception, not a precedent for panels.
 *
 * COLOR. Checked is the primary pair, which is the same statement Checkbox
 * makes when it fills: something here is on. Unchecked is a `line-strong`
 * track. The thumb takes an ink in both states -- `ink-subtle` off,
 * `primary-ink` on -- because the thumb is the mark ON the track, and there is
 * no token that is white in light and mid-grey in dark. Reading it as an ink
 * keeps the same contrast in both themes instead of dissolving into the dark
 * track.
 *
 * MOTION. The thumb translates 10px; the track cross-fades its fill. Nothing
 * else moves, and there is no press scale: a switch is the highest-frequency
 * control on a settings surface, and a control a user flips twice in a second
 * should not also bounce. Reduced motion drops the transition and the thumb
 * simply arrives.
 */

import { Switch as SwitchPrimitive } from "@base-ui/react/switch";

import { cn } from "./cn";

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "inline-flex h-3.5 w-6 shrink-0 items-center rounded-full bg-line-strong p-0.5 transition-colors duration-fast ease-out-quint outline-none select-none focus-visible:ring-2 focus-visible:ring-primary data-checked:bg-primary data-disabled:pointer-events-none data-disabled:opacity-50 motion-reduce:transition-none",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="size-2.5 rounded-full bg-ink-subtle transition-[translate,background-color] duration-fast ease-out-quint data-checked:translate-x-2.5 data-checked:bg-primary-ink motion-reduce:transition-none"
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
