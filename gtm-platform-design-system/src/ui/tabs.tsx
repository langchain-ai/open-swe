"use client";

/*
 * Tabs, retokenized onto CORE 14.
 *
 * SEGMENTED FAMILY. variant="default" is the same segmented-control family as
 * ToggleGroup: a 32px track at radius 10 with 2px padding on a `selected` fill,
 * holding 28px segments at radius 8. The active segment is `panel` fill plus a
 * `line-strong` hairline, no shadow — a segment already framed by its track
 * does not also need the raised-pressable rung. Unselected segments carry a
 * transparent border so selecting one moves no pixels. Geometry and selected
 * treatment match ToggleGroup; the APIs stay separate (tabs switch views with
 * panels, toggles filter without).
 *
 * GHOST. variant="ghost" is the floating-pill lane: no track fill, no
 * underline. Active clarity is a fully rounded (`rounded-full`) `panel` +
 * hairline pill — capsule geometry, same selected fill as segmented, without
 * the gray substrate behind the strip. Inbox status filters use this.
 *
 * LINE. variant="line" stays an underlined rail: no track fill, no selected
 * pill. Active clarity comes from ink text plus a 2px underline, not from a
 * segmented substrate. Do not force line tabs into the segmented family.
 *
 * DEPTH (thinned 2026-08-06 against fluid-functionalism tabs). Contrast and
 * the hairline do the selected job; stacking `shadow-control` on the trigger
 * itself recreated the thicker reading. The `line` variant stays flat for the
 * same reason: an underline tab is a label with a rule under it, not an object
 * on a track.
 *
 * MOTION. Color and border cross-fade at 160ms quint; nothing moves. A view
 * switch is high-frequency, so there is no press scale and no sliding indicator.
 */

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "./cn";

function Tabs({
  className,
  orientation = "horizontal",
  ...props
}: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      data-orientation={orientation}
      className={cn(
        "group/tabs flex gap-2 data-horizontal:flex-col",
        className
      )}
      {...props}
    />
  );
}

const tabsListVariants = cva(
  "group/tabs-list inline-flex w-fit items-center justify-center text-ink-subtle group-data-horizontal/tabs:h-control group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col",
  {
    variants: {
      variant: {
        default: "gap-0.5 rounded-control bg-selected p-0.5",
        ghost: "gap-0.5 rounded-none bg-transparent p-0",
        line: "gap-1 rounded-none bg-transparent p-0",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

function TabsList({
  className,
  variant = "default",
  ...props
}: TabsPrimitive.List.Props & VariantProps<typeof tabsListVariants>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(tabsListVariants({ variant }), className)}
      {...props}
    />
  );
}

function TabsTrigger({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-trigger"
      className={cn(
        "relative inline-flex h-control-sm flex-1 items-center justify-center gap-1.5 rounded-compact border border-transparent px-2.5 text-label font-medium whitespace-nowrap text-ink-subtle transition-[color,background-color,border-color] duration-fast ease-out-quint outline-none select-none group-data-vertical/tabs:w-full group-data-vertical/tabs:justify-start hover:text-ink focus-visible:ring-2 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-50 has-data-[icon=inline-end]:pr-1 has-data-[icon=inline-start]:pl-1 aria-disabled:pointer-events-none aria-disabled:opacity-50 motion-reduce:transition-none [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        "group-data-[variant=ghost]/tabs-list:rounded-full",
        "group-data-[variant=line]/tabs-list:h-control group-data-[variant=line]/tabs-list:rounded-none group-data-[variant=line]/tabs-list:bg-transparent group-data-[variant=line]/tabs-list:px-1 group-data-[variant=line]/tabs-list:data-active:border-transparent group-data-[variant=line]/tabs-list:data-active:bg-transparent",
        "data-active:border-line-strong data-active:bg-panel data-active:text-ink",
        "after:absolute after:bg-ink after:opacity-0 after:transition-opacity after:duration-fast after:ease-out-quint motion-reduce:after:transition-none group-data-horizontal/tabs:after:inset-x-0 group-data-horizontal/tabs:after:-bottom-px group-data-horizontal/tabs:after:h-0.5 group-data-vertical/tabs:after:inset-y-0 group-data-vertical/tabs:after:-right-px group-data-vertical/tabs:after:w-0.5 group-data-[variant=line]/tabs-list:data-active:after:opacity-100",
        className
      )}
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-content"
      className={cn("flex-1 text-body outline-none", className)}
      {...props}
    />
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants };
