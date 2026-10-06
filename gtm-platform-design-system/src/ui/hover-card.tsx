"use client";

/*
 * Hover card, on CORE 14. Base UI calls it a preview card; the product calls it
 * a hover card, and the product name wins at the boundary.
 *
 * CONTAINMENT. A preview is a panel floating over the canvas, which is exactly
 * where Popover and the dropdown menu already sit, so it takes the same step:
 * 12px radius, panel fill, ONE edge (`ring-1 ring-line-strong`), soft
 * `shadow-popup`. It is deliberately not the Tooltip surface. A tooltip is an
 * inverted label naming the thing under the cursor; a hover card is a small
 * document about it, with its own headings and links, and reading a document
 * out of an inverted chip is a different and worse thing.
 *
 * MOTION. The audited popup entrance, unchanged: opacity plus a 95% scale out
 * of `--transform-origin`, as a CSS transition so a pointer sweeping a column
 * of names gets one continuous movement rather than a keyframe restarted per
 * row. Never from zero, so the card grows out of the name it belongs to. The
 * exit is asymmetric (wiki 02, principle 6): it keeps the fade and drops the
 * scale, because a preview the pointer has already left should not still be
 * animating its way out of a list the reader is scanning past.
 *
 * TIMING. The delay is the 600ms family `TooltipProvider` sets, for the same
 * reason: a pointer merely crossing a table on its way somewhere else must not
 * open anything. The close delay is 300ms and is not optional, because the
 * reader has to be able to leave the trigger and land inside the card without
 * it evaporating under the cursor; a tooltip closes at 0 precisely because
 * nobody ever needs to point at one. Base UI hangs both on the trigger rather
 * than on a provider, so grouping is per trigger and there is nothing to mount
 * above the tree.
 */

import { PreviewCard as PreviewCardPrimitive } from "@base-ui/react/preview-card";

import { cn } from "./cn";
import { POPUP_SURFACE_PADDED } from "./popup-surface";

/** Same wait as the first tooltip in a group. Sweeping a list opens nothing. */
const HOVER_CARD_DELAY = 600;

/** Long enough to cross the gap from the trigger onto the card. */
const HOVER_CARD_CLOSE_DELAY = 300;

/*
 * 288px. A preview card holds a few lines about one object; wider and it reads
 * as a panel that should have been a route, narrower and a company name wraps.
 */
const CONTENT_CLASS = cn(
  "z-50 w-72 max-h-(--available-height)",
  POPUP_SURFACE_PADDED
);

function HoverCard({ ...props }: PreviewCardPrimitive.Root.Props) {
  return <PreviewCardPrimitive.Root {...props} />;
}

function HoverCardTrigger({
  delay = HOVER_CARD_DELAY,
  closeDelay = HOVER_CARD_CLOSE_DELAY,
  ...props
}: PreviewCardPrimitive.Trigger.Props) {
  return (
    <PreviewCardPrimitive.Trigger
      data-slot="hover-card-trigger"
      delay={delay}
      closeDelay={closeDelay}
      {...props}
    />
  );
}

function HoverCardContent({
  align = "center",
  alignOffset = 0,
  side = "bottom",
  sideOffset = 4,
  className,
  ...props
}: PreviewCardPrimitive.Popup.Props &
  Pick<
    PreviewCardPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  >) {
  return (
    <PreviewCardPrimitive.Portal>
      <PreviewCardPrimitive.Positioner
        className="isolate z-50 outline-none"
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
      >
        <PreviewCardPrimitive.Popup
          data-slot="hover-card-content"
          className={cn(CONTENT_CLASS, className)}
          {...props}
        />
      </PreviewCardPrimitive.Positioner>
    </PreviewCardPrimitive.Portal>
  );
}

export {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
  HOVER_CARD_CLOSE_DELAY,
  HOVER_CARD_DELAY,
};
