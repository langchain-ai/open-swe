"use client";

/*
 * Scroll area, on CORE 14. This file carries THE SCROLLBAR LAW (wiki 02):
 * "the thumb appears on hover or while scrolling and fades after; the track is
 * invisible (no gutter, no rail container, no permanent bar)". Every scrolling
 * region in the product routes through here, so the law is implemented once and
 * an app surface cannot express any other scrollbar.
 *
 * REVEAL. Base UI puts `data-hovering` on the scrollbar while the pointer is
 * over the scroll area and `data-scrolling` while the viewport is moving, so the
 * reveal is two variants over a resting `opacity-0` rather than a hover handler
 * and a timer of ours. The fade out is the same `duration-fast` transition
 * running in reverse, which is why this is a transition and not a keyframe: a
 * reader who flicks a long list twice gets one continuous fade instead of two
 * restarted animations.
 *
 * The previous form used `data-horizontal:` / `data-vertical:` variants, which
 * matched nothing: Base UI writes `data-orientation="horizontal"`, and Tailwind
 * compiles `data-horizontal:` to `[data-horizontal]`. Orientation geometry moved
 * into a lookup table keyed off the prop we already have, so there is no
 * attribute contract to get wrong.
 *
 * NO GUTTER. Base UI positions the scrollbar `position: absolute` against the
 * root and hides the native bar on the viewport itself, so the bar floats over
 * the content and the content column never narrows when a region becomes
 * scrollable. The one thing this file must not do is give the scrollbar a fill
 * or a border: either turns the overlay into a visible track. The pair of
 * transparent borders that used to sit here were exactly that, half-drawn.
 *
 * ABOVE STICKY CONTENT. A sticky group header (`z-10`) or a pinned grid cell
 * (`z-40`) is still content. Without a stacking trap those z-indexes escape the
 * viewport and paint over the overlay thumb, which is how Inbox account headers
 * hid the bar. The viewport is `isolate` so in-content stacking stays inside
 * the scrollport; the thumb is `z-10` so it sits above that isolated box. Never
 * raise a sticky header to beat the thumb, and never reserve a gutter to dodge
 * it.
 *
 * The viewport inherits whatever radius its container carries, which is a
 * relationship rather than a ladder step, so it stays a plain custom-property
 * declaration instead of becoming a `rounded-*` utility.
 *
 * TOUCH HAS NO HOVER, SO IT GETS NO REVEAL (ANALYSIS.md rows 11 and 45). On a
 * touch-primary device `data-hovering` never fires and `data-scrolling` fires
 * only while a flick is in progress, which leaves the reader with no standing
 * indication of where they are in a long list. The fallback is the thumb at
 * rest, visible, and nothing else changes: no fill, no border, no gutter, no
 * rail. The law being kept here is "the track is invisible", not "the thumb is
 * hidden", and the two are separable. It is still not `keepMounted`: a region
 * with nothing to scroll must show no bar at all, on any pointer.
 *
 * PERSISTENT IS A REVEAL SETTING, NOT A SECOND SCROLLBAR. Wide data is the one
 * surface wiki 02 lets keep a standing bar. That used to mean "keep the native
 * one", which is how a track and a gutter got back into the product through the
 * table's `always` mode. It now means `scrollbars="always"`: the same overlay
 * thumb, the same invisible track, resting at `opacity-100` instead of waiting
 * for a hover. One law, two reveal settings, no second scrollbar anywhere.
 *
 * THE LAW REACHES POPUPS THROUGH `ScrollAreaBody`, NOT THROUGH A CLASS. A Base
 * UI popup owns its own height (`max-h-(--available-height)`), so it cannot BE a
 * ScrollArea root - the root would have to carry that clamp, and a clamped root
 * only clips (see `viewportClassName` below). The alternative, letting a raw
 * `overflow-y-auto` box wear a hand-styled thumb, was rejected: `::-webkit-
 * scrollbar` styling switches Chrome and Safari out of overlay scrollbars into
 * the classic in-flow kind, so the very act of painting a nicer thumb reserves a
 * gutter and narrows the content column - the "weird container" this whole sweep
 * exists to delete. So the popup becomes a host (`SCROLL_HOST_CLASS`: it clamps
 * and clips) and the body inside it is a real ScrollArea that fills whatever the
 * host resolved to. Base UI marks Root, Viewport and Content `role="presentation"`
 * itself, which is what keeps this legal inside `role="menu"` and `role="listbox"`.
 */

import { useCallback } from "react";
import type { Ref } from "react";
import { useScrollRecovery } from "../lib/use-scroll-recovery";

import { ScrollArea as ScrollAreaPrimitive } from "@base-ui/react/scroll-area";

import { useTouchPrimary } from "../lib/use-touch-primary";

import { cn } from "./cn";

function assignViewportRef(ref: Ref<HTMLDivElement> | undefined, node: HTMLDivElement | null) {
  if (typeof ref === "function") return ref(node);
  if (ref !== undefined && ref !== null) ref.current = node;
}

type ScrollBarOrientation = "vertical" | "horizontal";

/** `hover` waits for the pointer; `always` rests visible. Never a native bar. */
type ScrollbarReveal = "hover" | "always";

/* Geometry and behaviour that hold on every pointer. Opacity is not in here. */
const SCROLLBAR_CLASS = "z-10 flex touch-none p-px select-none";

/*
 * The pointer reveal. The resting state is invisible; `opacity-0` plus the two
 * variants is the whole law, and there is no fill or border anywhere on it.
 */
const SCROLLBAR_HOVER_REVEAL_CLASS =
  "opacity-0 transition-opacity duration-fast ease-out-quint data-hovering:opacity-100 data-scrolling:opacity-100 motion-reduce:transition-none";

/*
 * At rest and visible. Two callers, one class: the touch fallback (no hover to
 * wait for) and `scrollbars="always"` (wide data asked for a standing bar).
 */
const SCROLLBAR_RESTING_CLASS = "opacity-100";

/*
 * Cross-axis thickness only. Base UI pins the other two edges and the corner
 * inset itself, so an orientation never needs an offset from us.
 */
const SCROLLBAR_ORIENTATION_CLASS: Record<ScrollBarOrientation, string> = {
  vertical: "h-full w-2.5",
  horizontal: "h-2.5 flex-col",
};

/** The thumb is the only visible part, and `line-strong` is the hairline weight. */
const SCROLLBAR_THUMB_CLASS = "relative flex-1 rounded-full bg-line-strong";

const VIEWPORT_CLASS =
  "isolate size-full [border-radius:inherit] transition-[color,box-shadow] duration-fast ease-out-quint outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-1 motion-reduce:transition-none";

interface ScrollBarProps extends ScrollAreaPrimitive.Scrollbar.Props {
  /** `always` pins the thumb visible; the track stays invisible either way. */
  reveal?: ScrollbarReveal;
}

function ScrollBar({
  className,
  orientation = "vertical",
  reveal = "hover",
  ...props
}: ScrollBarProps) {
  const touchPrimary = useTouchPrimary();
  const resting = touchPrimary || reveal === "always";

  return (
    <ScrollAreaPrimitive.Scrollbar
      data-slot="scroll-area-scrollbar"
      data-touch-primary={touchPrimary}
      data-reveal={reveal}
      orientation={orientation}
      className={cn(
        SCROLLBAR_CLASS,
        resting ? SCROLLBAR_RESTING_CLASS : SCROLLBAR_HOVER_REVEAL_CLASS,
        SCROLLBAR_ORIENTATION_CLASS[orientation],
        className
      )}
      {...props}
    >
      <ScrollAreaPrimitive.Thumb
        data-slot="scroll-area-thumb"
        className={SCROLLBAR_THUMB_CLASS}
      />
    </ScrollAreaPrimitive.Scrollbar>
  );
}

/**
 * Which axes may scroll. Default `both` for wide data (tables, gallery mats).
 * Pickers and menus pass `vertical` — they should adapt to width, never grow a
 * horizontal thumb because a flex row refused to shrink.
 */
type ScrollAreaOverflow = "vertical" | "horizontal" | "both";

interface ScrollAreaProps extends ScrollAreaPrimitive.Root.Props {
  /**
   * Classes for the scrolling element rather than the root.
   *
   * A bounded scroll region caps the height of the box that scrolls, and that
   * box is the viewport, not the root: `max-h-*` on the root only clips,
   * because the viewport's `h-full` resolves to `auto` against an auto-height
   * parent and the content spills straight past it.
   */
  viewportClassName?: string;
  /** The scrolling element, for a consumer that reads scroll offsets itself. */
  viewportRef?: Ref<HTMLDivElement>;
  /** Stable product region identity; transient popups do not opt in. */
  recoveryKey?: string;
  /** `hover` (default) or the standing thumb wide data may opt into. */
  scrollbars?: ScrollbarReveal;
  /** Axes that may scroll. Default `both`. */
  overflow?: ScrollAreaOverflow;
}

function ScrollArea({
  className,
  viewportClassName,
  viewportRef,
  recoveryKey,
  scrollbars = "hover",
  overflow = "both",
  children,
  ...props
}: ScrollAreaProps) {
  const recoveryRef = useScrollRecovery(recoveryKey);
  const combinedRef = useCallback((node: HTMLDivElement | null) => {
    recoveryRef(node);
    return assignViewportRef(viewportRef, node);
  }, [recoveryRef, viewportRef]);
  const allowVertical = overflow === "vertical" || overflow === "both";
  const allowHorizontal = overflow === "horizontal" || overflow === "both";

  return (
    <ScrollAreaPrimitive.Root
      data-slot="scroll-area"
      data-overflow={overflow}
      className={cn("relative", className)}
      {...props}
    >
      <ScrollAreaPrimitive.Viewport
        data-slot="scroll-area-viewport"
        ref={combinedRef}
        className={cn(
          VIEWPORT_CLASS,
          overflow === "vertical" && "overflow-x-hidden!",
          overflow === "horizontal" && "overflow-y-hidden!",
          viewportClassName
        )}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      {/*
       * NOT keepMounted. With it, the bar element exists on every region and
       * `data-hovering` fires regardless of overflow, so hovering anything
       * wrapped in a ScrollArea showed a full-length thumb even with nothing
       * to scroll (Amal hit this in the gallery: "scroll bars appear
       * everywhere"). Without it, Base UI unmounts the bar entirely when the
       * region is not scrollable, and a scrollable region keeps its bar
       * mounted at `opacity-0` - so the hover/scroll reveal still fades in
       * through the transition rather than popping.
       */}
      {allowVertical ? (
        <ScrollBar orientation="vertical" reveal={scrollbars} />
      ) : null}
      {allowHorizontal ? (
        <ScrollBar orientation="horizontal" reveal={scrollbars} />
      ) : null}
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
}

/*
 * The class a box wears to become a scroll HOST: it owns the height (its own
 * `max-h-*`, or a definite one from its parent) and clips, and a `ScrollAreaBody`
 * inside it does the scrolling. Column flex is the load-bearing half - it is the
 * only way a child can resolve a definite height against a parent that is merely
 * capped, which is what every anchored popup is.
 */
const SCROLL_HOST_CLASS = "flex flex-col overflow-hidden";

/*
 * The scroll region inside a `SCROLL_HOST_CLASS` box.
 *
 * THE VIEWPORT CANNOT BE `h-full` HERE, AND THAT IS THE WHOLE REASON THIS
 * COMPONENT EXISTS. A host that is merely capped (`max-h-*`, height `auto`) has
 * an indefinite height, so a percentage resolved against it falls back to
 * `auto`: the flex chain shrinks the ROOT to 248px exactly as intended, and then
 * `size-full` on the viewport inside it computes to the content's 960px and the
 * region silently never scrolls. Measured, not reasoned: a menu of forty items
 * sat clipped at `scrollHeight === clientHeight`.
 *
 * The fix is one more flex line rather than a height. The root becomes a column
 * of its own and the viewport is its `flex-1 min-h-0` child, so the viewport's
 * height comes from flex layout instead of from a percentage that has nothing to
 * be a percentage of. `h-auto` is what releases `size-full`'s half of that.
 *
 * `flex-auto` on the root, not `flex-1`: with `flex-basis: 0%` a short popup
 * still measures right, but the intent here is "size to the content, shrink
 * under the cap", and `flex: 1 1 auto` says that directly.
 */
const BODY_VIEWPORT_CLASS = "h-auto w-full min-h-0 flex-1";

function ScrollAreaBody({
  className,
  viewportClassName,
  ...props
}: ScrollAreaProps) {
  return (
    <ScrollArea
      data-slot="scroll-area-body"
      className={cn("flex min-h-0 flex-auto flex-col", className)}
      viewportClassName={cn(BODY_VIEWPORT_CLASS, viewportClassName)}
      {...props}
    />
  );
}

export { ScrollArea, ScrollAreaBody, ScrollBar, SCROLL_HOST_CLASS };
export type {
  ScrollAreaOverflow,
  ScrollAreaProps,
  ScrollBarOrientation,
  ScrollBarProps,
  ScrollbarReveal,
};
