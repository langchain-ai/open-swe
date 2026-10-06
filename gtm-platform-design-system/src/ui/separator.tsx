"use client";

/*
 * Separator, retokenized onto CORE 14.
 *
 * A divider takes the lighter of the two line weights; `line-strong` is
 * reserved for the edge of a control, where a hairline has to hold its own
 * against a fill.
 */

import { Separator as SeparatorPrimitive } from "@base-ui/react/separator";

import { cn } from "./cn";

function Separator({
  className,
  orientation = "horizontal",
  ...props
}: SeparatorPrimitive.Props) {
  return (
    <SeparatorPrimitive
      data-slot="separator"
      orientation={orientation}
      className={cn(
        "shrink-0 bg-line data-horizontal:h-px data-horizontal:w-full data-vertical:w-px data-vertical:self-stretch",
        className
      )}
      {...props}
    />
  );
}

export { Separator };
