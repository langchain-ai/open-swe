/*
 * IconWell — the 24px container every channel mark and tool step icon sits in.
 *
 * One geometry for ProviderMark, ToolCallCard, and gallery specimens: 24px
 * (`size-6`) on `rounded-compact`, muted fill, hairline. The glyph/logo inside
 * is 14px so brand marks and Heroicons share a lane and labels never shift.
 */

import type { ComponentProps } from "react";

import { cn } from "./cn";

const ICON_WELL_CLASS =
  "inline-flex size-6 shrink-0 items-center justify-center rounded-compact border border-line bg-muted [&_svg]:size-3.5";

type IconWellProps = ComponentProps<"span"> & {
  /** Accessible name when the well stands alone. */
  label?: string;
};

function IconWell({ children, className, label, ...props }: IconWellProps) {
  return (
    <span
      className={cn(ICON_WELL_CLASS, className)}
      aria-label={label}
      title={label}
      data-slot="icon-well"
      {...props}
    >
      {children}
    </span>
  );
}

export { IconWell, ICON_WELL_CLASS };
export type { IconWellProps };
