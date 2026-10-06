"use client";

/*
 * Popover, on CORE 14.
 *
 * Containment follows commitment: the popup is a panel (12px radius, panel
 * fill, hairline ring) floating over the canvas, which is exactly the dropdown
 * menu's containment step, so the two read as one family when a filter chip
 * opens one and a row overflow opens the other. Entrances are ease-out from
 * `--transform-origin` at 95%, never from zero, so the popup grows out of its
 * trigger instead of appearing from nothing.
 *
 * DEPTH. `shadow-popup` plus ONE edge (`ring-1 ring-line-strong`). Shared with
 * the menu, the select list and the hover card: four things that float one step
 * off the page are one family. The popup rung is a soft float (same drop stack
 * as a primary's hover lift), not a modal stack -- the heavy rung is overlay,
 * for dialogs and sheets. Never add a second shadow or a shadow-painted ring;
 * see ELEVATION_RULES.
 *
 * The exit is asymmetric (wiki 02, principle 6): the entrance grows out of the
 * trigger, the exit only fades. Dropping the scale from the ending style is
 * what makes a dismissal read as final rather than as the opening rewound, and
 * on a front-loaded quint it is visually over in about a third of the clock.
 *
 * The enter is a CSS transition on `data-starting-style` / `data-ending-style`
 * rather than a tw-animate-css keyframe. Two reasons, both load-bearing. A
 * transition retargets when the user reverses it mid-flight; a keyframe
 * restarts from its first frame, so a filter chip clicked twice quickly pops.
 * And `duration-fast` sets `transition-duration` only, so the keyframe form was
 * silently running at tw-animate-css's 150ms default with the browser's `ease`
 * curve -- an ease-in ramp on an entrance, which the design law forbids.
 *
 * A POPUP DOES NOT SCROLL; ITS BODY DOES.
 *
 * The popup used to be a raw `overflow-y-auto` box, so a tall popover showed the
 * platform's native bar with its track and its gutter -- the one scrollbar shape
 * wiki 02 forbids outright. The fix is not to move that overflow onto the popup
 * under a nicer name. It is to say which box owns scrolling, once, for every
 * anchored popup in the system: the popup clamps to `--available-height` and
 * clips, and whatever fills it is the scroll surface.
 *
 * A dropdown menu and a select fill themselves, so those two wrap their own
 * items in a `ScrollAreaBody` unconditionally. A popover's content belongs to
 * the caller, and every caller in the product today -- Combobox, DatePicker, the
 * filter popovers -- brings a body that already bounds and scrolls itself (a
 * Command list, a Calendar). Wrapping those in a second scroll region would nest
 * a viewport inside a viewport for nothing. So the popover's own body region is
 * `scrollable`, opt-in, for the caller whose content is plain and tall.
 *
 * That is a real default change: a tall popover with unbounded content now clips
 * where it used to scroll natively. It is the right way round. Clipping is
 * visible in review the moment it happens, and one word fixes it; a native bar
 * is invisible to whoever built the surface on a machine set to overlay
 * scrollbars, and lands on the reader as the weird grey container.
 *
 * INSET. Default is `padded` (POPUP_SURFACE_PADDED) for small documents.
 * Pickers whose body owns spacing — Combobox (Command), DatePicker (Calendar)
 * — pass `inset="flush"` so they share the shell without fighting `p-3` with a
 * call-site `p-0`. When the popup itself scrolls (`scrollable`), padding stays
 * on so the thumb rides inside the inset rather than against the rounded edge.
 */

import { Popover as PopoverPrimitive } from "@base-ui/react/popover";

import { cn } from "./cn";
import {
  POPUP_SURFACE_FLUSH,
  POPUP_SURFACE_PADDED,
} from "./popup-surface";
import { ScrollAreaBody, SCROLL_HOST_CLASS } from "./scroll-area";

function Popover({ ...props }: PopoverPrimitive.Root.Props) {
  return <PopoverPrimitive.Root data-slot="popover" {...props} />;
}

function PopoverTrigger({ ...props }: PopoverPrimitive.Trigger.Props) {
  return <PopoverPrimitive.Trigger data-slot="popover-trigger" {...props} />;
}

function PopoverClose({ ...props }: PopoverPrimitive.Close.Props) {
  return <PopoverPrimitive.Close data-slot="popover-close" {...props} />;
}

function PopoverArrow(props: PopoverPrimitive.Arrow.Props) {
  return <PopoverPrimitive.Arrow data-slot="popover-arrow" {...props} />;
}

type PopoverInset = "padded" | "flush";

type PopoverContentProps = PopoverPrimitive.Popup.Props &
  Pick<
    PopoverPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset" | "anchor"
  > & {
    /**
     * Popup padding contract from `popup-surface.ts`.
     *
     * - `padded` (default) — small documents; `p-3`.
     * - `flush` — Combobox / DatePicker / any body that owns its own inset.
     */
    inset?: PopoverInset;
    /**
     * Give the popup body its own overlay scroll region.
     *
     * Off by default: content that bounds itself (a Command list, a Calendar)
     * already scrolls, and a second region around it is a viewport inside a
     * viewport. Turn it on when the content is plain and may outgrow the popup.
     */
    scrollable?: boolean;
  };

function PopoverContent({
  anchor,
  align = "center",
  alignOffset = 0,
  side = "bottom",
  sideOffset = 4,
  inset = "padded",
  scrollable = false,
  className,
  children,
  ...props
}: PopoverContentProps) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner
        anchor={anchor}
        className="isolate z-50 outline-none"
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
      >
        <PopoverPrimitive.Popup
          data-slot="popover-content"
          data-inset={inset}
          className={cn(
            "z-50 max-h-(--available-height) overflow-hidden",
            inset === "flush" ? POPUP_SURFACE_FLUSH : POPUP_SURFACE_PADDED,
            scrollable && SCROLL_HOST_CLASS,
            className
          )}
          {...props}
        >
          {scrollable ? (
            <ScrollAreaBody overflow="vertical">
              {children}
            </ScrollAreaBody>
          ) : children}
        </PopoverPrimitive.Popup>
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  );
}

export { Popover, PopoverTrigger, PopoverContent, PopoverClose, PopoverArrow };
export type { PopoverContentProps };
