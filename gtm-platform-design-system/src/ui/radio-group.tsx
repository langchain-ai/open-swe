"use client";

/*
 * Radio group, on CORE 14.
 *
 * THE ONE SANCTIONED FULL-ROUND EXCEPTION. Every other control in this
 * directory takes a corner off the radius ladder (6 badge / 8 compact / 10
 * control / 12 panel / 14 shell) and `rounded-full` is otherwise a violation.
 * A radio is round the way an avatar is round: the circle is not a very large
 * corner radius, it is the shape that tells a user this choice is exclusive
 * where the square next to it is not. Give a radio `rounded-badge` and it
 * becomes a checkbox that behaves surprisingly. So: `size-4 rounded-full`,
 * stated here so nobody has to rediscover it, and not a licence for anything
 * that is merely small.
 *
 * GEOMETRY AND COLOR follow Checkbox exactly, because the two sit in the same
 * rows and answer the same kind of question: a 16px box (smaller than both the
 * 28px row and the 32px control beside it), a `line-strong` hairline over a
 * `muted` fill when empty, and the primary pair when selected -- the ring is
 * `border-primary` over `bg-primary`, the dot is `primary-ink`. One fill, one
 * mark, the same statement Checkbox makes: something here is selected.
 *
 * MOTION. The press scale matches Button and Checkbox, so a radio and the
 * button beside it react alike; the dot fades rather than scaling in, since a
 * 6px disc growing from nothing reads as a glitch at this size.
 */

import { Radio as RadioPrimitive } from "@base-ui/react/radio";
import { RadioGroup as RadioGroupPrimitive } from "@base-ui/react/radio-group";

import { cn } from "./cn";

function RadioGroup<Value>({
  className,
  ...props
}: RadioGroupPrimitive.Props<Value>) {
  return (
    <RadioGroupPrimitive
      data-slot="radio-group"
      className={cn("grid gap-2", className)}
      {...props}
    />
  );
}

function RadioGroupItem<Value>({
  className,
  ...props
}: RadioPrimitive.Root.Props<Value>) {
  return (
    <RadioPrimitive.Root
      data-slot="radio-group-item"
      className={cn(
        "inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-line-strong bg-muted transition-[scale,background-color,border-color] duration-fast ease-out-quint outline-none select-none focus-visible:ring-2 focus-visible:ring-primary active:scale-[0.97] data-checked:border-primary data-checked:bg-primary data-disabled:pointer-events-none data-disabled:opacity-50 motion-reduce:transition-none",
        className
      )}
      {...props}
    >
      <RadioPrimitive.Indicator
        data-slot="radio-group-indicator"
        className="size-1.5 rounded-full bg-primary-ink transition-opacity duration-fast ease-out-quint data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none"
      />
    </RadioPrimitive.Root>
  );
}

export { RadioGroup, RadioGroupItem };
