/*
 * Skeleton, retokenized onto CORE 14.
 *
 * The fill is `selected`, not `muted`: a placeholder has to read as a block of
 * pending content, and `muted` sits half a step off `panel` in the light theme,
 * which left the vendored version invisible on white.
 *
 * The pulse is an indeterminate loop, so it stops under `prefers-reduced-motion`
 * and the placeholder holds its fill. A block of pending content still reads as
 * pending without the breathing.
 */

import { cn } from "./cn";

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        "animate-pulse rounded-compact bg-selected motion-reduce:animate-none",
        className
      )}
      {...props}
    />
  );
}

export { Skeleton };
