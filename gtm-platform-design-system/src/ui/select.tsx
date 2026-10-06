"use client";

/*
 * Select, on CORE 14.
 *
 * The trigger is a control, so it takes control geometry (32px, 10px radius,
 * `line-strong` hairline) and reads as a sibling of Input and Button outline in
 * a toolbar. The hairline IS its edge, so the trigger grows no shadow -- the
 * same ruling as Button outline (Amal 2026-08-06). The popup is the panel step
 * above it, and the items inside drop one more to compact, which keeps the
 * inner radius concentric across the 4px of popup padding.
 *
 * DEPTH. The popup takes `shadow-popup` plus ONE edge (`ring-1 ring-line-strong`),
 * the soft anchored-float rung shared with popover/menu/hover-card. It is a
 * fixed rung rather than a computed one for the reason `<Elevated>`'s
 * `shadowLevel` override exists: a picker opened inside a dialog is still a
 * picker, so its weight should not change just because its ground did.
 *
 * The trigger carries Button's size axis, `control` (32 / 10px) and `compact`
 * (28 / 8px), for the same reason Button does: placement picks the size, and a
 * select standing in a pagination strip beside 28px page buttons is a different
 * control from one standing in a 32px toolbar. It is a CVA variant rather than
 * a call-site override because `cn` cannot resolve `h-control` against
 * `h-control-sm` -- tailwind-merge does not know the CORE 14 token names, so an
 * override is settled by stylesheet order and silently lands a 28px control
 * wearing a 10px corner.
 *
 * `alignItemWithTrigger` is off. Base UI defaults to the native macOS behaviour
 * of laying the selected item over the trigger; a dense product table wants the
 * predictable dropdown that lands under the control and never covers the row
 * the user just clicked.
 *
 * MOTION. Same gesture as the dropdown menu, for the same reason: opacity and a
 * 95% scale out of `--transform-origin`, expressed as a CSS transition so a
 * user who opens and closes the picker twice in a second gets one continuous
 * movement rather than two restarted keyframes. The exit is the asymmetric one
 * wiki 02 states: a plain fade, never the entrance played backwards.
 *
 * ACKNOWLEDGE, THEN CLOSE. Picking an item used to unmount the popup in the
 * same frame the checkmark was drawn on the chosen row, so the confirmation
 * existed only in the DOM and never on screen: the user saw a menu vanish and
 * had to read the trigger to learn what they had picked. The close is now held
 * for one `duration-fast` beat, which is long enough to see the indicator land
 * and short enough that nobody waits for it. It ends where the old code ended.
 *
 * The hold needs `open` under our control, because Base UI closes itself on an
 * item press and there is no imperative "close later". So this wrapper owns the
 * open state, falls back to `defaultOpen`, and forwards every change to a
 * caller's `onOpenChange` -- including the deferred one, so a controlled parent
 * sees the same single close it saw before, just a beat later. Only the
 * `item-press` reason is deferred: Escape, an outside press and a second press
 * on the trigger are dismissals, and a dismissal that lingers reads as latency.
 *
 * OVERFLOW. A picker with more options than fit used to scroll on the popup
 * itself with a raw `overflow-y-auto`, which is the platform bar with its track
 * and its gutter. The popup is a scroll HOST now and a `ScrollAreaBody` inside
 * carries the overlay thumb, on the same terms as the dropdown menu: Base UI
 * marks the scroll area `role="presentation"` so the popup keeps owning its
 * `option`s, and the highlighted-item `scrollIntoView` finds the viewport as its
 * nearest scrollable ancestor.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Select as SelectPrimitive } from "@base-ui/react/select";
import { cva, type VariantProps } from "class-variance-authority";

import { Check, ChevronDown } from "./glyphs";
import { cn } from "./cn";
import { FIELD_FOCUS_CLASS } from "./field-focus";
import { Icon } from "./icon";
import { FIELD_VALUE_CLASS } from "./input";
import { POPUP_SURFACE_DENSE } from "./popup-surface";
import { ScrollAreaBody, SCROLL_HOST_CLASS } from "./scroll-area";

/** Kept in sync by hand with `duration-fast` (160ms) in globals.css. */
const ACKNOWLEDGE_MS = 160;

/** The one close Base UI reports that is a confirmation rather than a dismissal. */
const ITEM_PRESS_REASON = "item-press";

const selectTriggerVariants = cva(
  cn(
    "flex w-fit items-center justify-between gap-1.5 border border-line-strong bg-panel px-2.5 py-1 whitespace-nowrap text-ink transition-[scale,background-color,border-color] duration-fast ease-out-quint outline-none select-none hover:bg-hover active:scale-[0.97] data-disabled:pointer-events-none data-disabled:opacity-50 data-popup-open:bg-hover motion-reduce:transition-none [&_svg]:pointer-events-none [&_svg]:shrink-0",
    FIELD_VALUE_CLASS,
    FIELD_FOCUS_CLASS
  ),
  {
    variants: {
      size: {
        control: "h-control rounded-control",
        compact: "h-control-sm rounded-compact",
      },
    },
    defaultVariants: {
      size: "control",
    },
  }
);

function Select<Value, Multiple extends boolean | undefined = false>({
  open,
  defaultOpen = false,
  onOpenChange,
  ...props
}: SelectPrimitive.Root.Props<Value, Multiple>) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const holdRef = useRef<number | null>(null);

  const isControlled = open !== undefined;
  const resolvedOpen = isControlled ? open : uncontrolledOpen;

  useEffect(() => {
    return () => {
      if (holdRef.current !== null) window.clearTimeout(holdRef.current);
    };
  }, []);

  const commit = useCallback(
    (next: boolean, details: SelectPrimitive.Root.ChangeEventDetails) => {
      if (!isControlled) setUncontrolledOpen(next);
      onOpenChange?.(next, details);
    },
    [isControlled, onOpenChange]
  );

  const handleOpenChange = useCallback(
    (next: boolean, details: SelectPrimitive.Root.ChangeEventDetails) => {
      if (holdRef.current !== null) {
        window.clearTimeout(holdRef.current);
        holdRef.current = null;
      }
      if (next || details.reason !== ITEM_PRESS_REASON) {
        commit(next, details);
        return;
      }
      holdRef.current = window.setTimeout(() => {
        holdRef.current = null;
        commit(false, details);
      }, ACKNOWLEDGE_MS);
    },
    [commit]
  );

  return (
    <SelectPrimitive.Root
      data-slot="select"
      open={resolvedOpen}
      onOpenChange={handleOpenChange}
      {...props}
    />
  );
}

function SelectGroup({ ...props }: SelectPrimitive.Group.Props) {
  return <SelectPrimitive.Group data-slot="select-group" {...props} />;
}

function SelectValue({ className, ...props }: SelectPrimitive.Value.Props) {
  return (
    <SelectPrimitive.Value
      data-slot="select-value"
      className={cn("truncate text-left", className)}
      {...props}
    />
  );
}

function SelectTrigger({
  className,
  children,
  size = "control",
  ...props
}: SelectPrimitive.Trigger.Props & VariantProps<typeof selectTriggerVariants>) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      data-size={size}
      className={cn(selectTriggerVariants({ size }), className)}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon data-slot="select-icon">
        <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function SelectContent({
  align = "start",
  alignOffset = 0,
  side = "bottom",
  sideOffset = 4,
  className,
  children,
  ...props
}: SelectPrimitive.Popup.Props &
  Pick<
    SelectPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  >) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Positioner
        className="isolate z-50 outline-none"
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
        alignItemWithTrigger={false}
      >
        <SelectPrimitive.Popup
          data-slot="select-content"
          className={cn(
            "z-50 max-h-(--available-height) min-w-(--anchor-width)",
            POPUP_SURFACE_DENSE,
            SCROLL_HOST_CLASS,
            className
          )}
          {...props}
        >
          <ScrollAreaBody overflow="vertical">{children}</ScrollAreaBody>
        </SelectPrimitive.Popup>
      </SelectPrimitive.Positioner>
    </SelectPrimitive.Portal>
  );
}

function SelectLabel({ className, ...props }: SelectPrimitive.GroupLabel.Props) {
  return (
    <SelectPrimitive.GroupLabel
      data-slot="select-label"
      className={cn(
        "px-1.5 py-1 text-meta font-medium text-ink-subtle",
        className
      )}
      {...props}
    />
  );
}

function SelectItem({
  className,
  children,
  ...props
}: SelectPrimitive.Item.Props) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "relative flex cursor-default items-center gap-1.5 rounded-badge py-1 pr-8 pl-1.5 text-label outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50 data-highlighted:bg-hover data-highlighted:text-ink [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
    >
      <SelectPrimitive.ItemIndicator
        data-slot="select-item-indicator"
        className="pointer-events-none absolute right-2 flex items-center justify-center"
      >
        <Icon icon={Check} />
      </SelectPrimitive.ItemIndicator>
      <SelectPrimitive.ItemText
        data-slot="select-item-text"
        className="inline-flex min-w-0 items-center gap-1.5"
      >
        {children}
      </SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

function SelectSeparator({
  className,
  ...props
}: SelectPrimitive.Separator.Props) {
  return (
    <SelectPrimitive.Separator
      data-slot="select-separator"
      className={cn("-mx-1 my-1 h-px bg-line", className)}
      {...props}
    />
  );
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
};
