"use client";

/*
 * Checkbox, on CORE 14.
 *
 * A checkbox is not a step on the density ladder: it lives inside a 28px row or
 * beside a 32px control and has to stay smaller than both, so it is a 16px
 * square carrying the smallest radius in the scale (6px badge). Checked and
 * indeterminate share one fill, the primary pair, because they make the same
 * statement about the row: something here is selected. The press scale matches
 * Button, so a checkbox and the button next to it in a toolbar react alike.
 */

import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox";

import { Check, Minus } from "./glyphs";
import { cn } from "./cn";
import { Icon } from "./icon";

function Checkbox({
  className,
  indeterminate = false,
  ...props
}: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      indeterminate={indeterminate}
      className={cn(
        "inline-flex size-4 shrink-0 items-center justify-center rounded-tick border border-line-strong bg-muted text-primary-ink transition-[scale,background-color,border-color] duration-fast ease-out-quint outline-none select-none focus-visible:ring-2 focus-visible:ring-primary active:scale-[0.97] data-checked:border-primary data-checked:bg-primary data-disabled:pointer-events-none data-disabled:opacity-50 data-indeterminate:border-primary data-indeterminate:bg-primary motion-reduce:transition-none",
        className
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        data-slot="checkbox-indicator"
        className="flex items-center justify-center text-current transition-opacity duration-fast ease-out-quint data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none"
      >
        <Icon icon={indeterminate ? Minus : Check} size="sm" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
