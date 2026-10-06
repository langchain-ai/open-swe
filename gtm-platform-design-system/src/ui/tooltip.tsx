"use client";

/*
 * Tooltip, retokenized onto CORE 14.
 *
 * The popup is the one deliberately inverted surface in the system: it reads as
 * a label floating above the page rather than a panel sitting on it, so it takes
 * the secondary role pair (secondary / secondary-ink) instead of panel / ink.
 * The pair flips with the theme, so there is no dark branch to keep in sync.
 *
 * TIMING. The first tooltip in a group waits; the rest do not. `delay` is the
 * wait before the first one opens, and `timeout` is how long after one closes
 * that its neighbours still open instantly -- so sweeping an icon rail reads as
 * one label tracking the cursor rather than six popups firing in sequence, and
 * a pointer merely crossing a toolbar on its way somewhere else never opens
 * anything. The vendored default was `delay = 0`, which made every tooltip in
 * the product fire on contact: the single loudest thing a tooltip can do wrong.
 * Both values need the shared `TooltipProvider` to be mounted above them; a
 * bare `Tooltip` falls back to Base UI's own 600ms and groups with nothing.
 *
 * MOTION. The exit is asymmetric (wiki 02, principle 6): the entrance grows out
 * of `--transform-origin` at 95%, the exit only fades. A label whose whole job
 * is to be transient must not be the last thing still moving after the pointer
 * has gone somewhere else.
 */

import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip";

import { cn } from "./cn";

function TooltipProvider({
  delay = 600,
  closeDelay = 0,
  timeout = 400,
  ...props
}: TooltipPrimitive.Provider.Props) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delay={delay}
      closeDelay={closeDelay}
      timeout={timeout}
      {...props}
    />
  );
}

function Tooltip({ ...props }: TooltipPrimitive.Root.Props) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />;
}

function TooltipTrigger({ ...props }: TooltipPrimitive.Trigger.Props) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />;
}

function TooltipContent({
  className,
  side = "top",
  sideOffset = 4,
  align = "center",
  alignOffset = 0,
  children,
  ...props
}: TooltipPrimitive.Popup.Props &
  Pick<
    TooltipPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  >) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Positioner
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
        className="isolate z-50"
      >
        <TooltipPrimitive.Popup
          data-slot="tooltip-content"
          className={cn(
            "z-50 inline-flex w-fit max-w-xs origin-(--transform-origin) items-center gap-1.5 rounded-badge bg-secondary px-3 py-1.5 text-meta text-secondary-ink transition-[opacity,scale] duration-fast ease-out-quint has-data-[slot=kbd]:pr-1.5 **:data-[slot=kbd]:relative **:data-[slot=kbd]:isolate **:data-[slot=kbd]:z-50 **:data-[slot=kbd]:rounded-badge data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none",
            className
          )}
          {...props}
        >
          {children}
          {/*
           * The arrow is a 10px square rotated 45deg. It carries no radius:
           * the smallest ladder step (6px badge) would round a 10px square into
           * a blob, and 2px on a tip this size is not visible anyway.
           */}
          <TooltipPrimitive.Arrow className="z-50 size-2.5 translate-y-[calc(-50%-2px)] rotate-45 bg-secondary fill-secondary data-[side=bottom]:top-1 data-[side=inline-end]:top-1/2! data-[side=inline-end]:-left-1 data-[side=inline-end]:-translate-y-1/2 data-[side=inline-start]:top-1/2! data-[side=inline-start]:-right-1 data-[side=inline-start]:-translate-y-1/2 data-[side=left]:top-1/2! data-[side=left]:-right-1 data-[side=left]:-translate-y-1/2 data-[side=right]:top-1/2! data-[side=right]:-left-1 data-[side=right]:-translate-y-1/2 data-[side=top]:-bottom-2.5" />
        </TooltipPrimitive.Popup>
      </TooltipPrimitive.Positioner>
    </TooltipPrimitive.Portal>
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
