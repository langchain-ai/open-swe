"use client";

/*
 * Dropdown menu, retokenized onto CORE 14.
 *
 * Containment follows commitment: the popup is a panel (12px) and the items
 * nested inside it drop one step to compact (8px), so the inner radius stays
 * concentric with the outer one across the 4px of popup padding. The
 * destructive variant is the risk pair, which flips with the theme and replaces
 * the vendored dark-only focus-tint branch.
 *
 * DEPTH. The popup takes `shadow-popup` plus ONE edge (`ring-1 ring-line-strong`):
 * the soft anchored-float treatment shared with popover/select/hover-card, not
 * the modal overlay stack. The submenu takes the same rung rather than climbing
 * to overlay -- a submenu is beside its parent, not further from the page, and
 * the extra weight only made the second column look like a different kind of
 * object. See ELEVATION_RULES for when a rung is legal.
 *
 * MOTION. A menu is the highest-frequency popup in the product, so it gets the
 * smallest honest gesture: opacity plus a 95% scale out of the anchor, and
 * nothing else. The per-side `slide-in-from-*` offsets are gone -- an origin
 * scale already tells the eye where the popup came from, and stacking a slide
 * on top of it reads as two animations for one event. It is a CSS transition
 * rather than a tw-animate-css keyframe because a menu is the thing a user
 * reverses fastest: a transition retargets from wherever it is, a keyframe
 * restarts from zero every time it is retriggered.
 *
 * The exit is asymmetric (wiki 02, principle 6): the scale belongs to the
 * entrance alone and the exit is a plain fade. On the highest-frequency popup
 * in the product that is the difference between a menu that gets out of the way
 * and a menu that has to finish shrinking before the next click lands.
 *
 * OVERFLOW. A menu long enough to hit `--available-height` used to scroll on the
 * popup itself with a raw `overflow-y-auto`, which is the platform bar, its
 * track and its gutter -- the exact shape wiki 02 bans. The popup is a scroll
 * HOST now and a `ScrollAreaBody` inside it carries the overlay thumb
 * (`overflow="vertical"` only — a menu adapts to width, it never grows a
 * horizontal thumb). This is legal inside `role="menu"` because Base UI marks
 * the scroll area's root, viewport and content `role="presentation"`, so the
 * popup still owns its `menuitem`s as far as assistive tech is concerned; and
 * it leaves keyboard navigation alone, because the roving-focus
 * `scrollIntoView` walks up to the nearest scrollable ancestor, which is now
 * the viewport.
 *
 * WIDTH. Match Select: `min-w-(--anchor-width)`, never `w-(--anchor-width)`.
 * Locking width to the anchor made icon-trigger menus (⋯ on a thread row)
 * compute at ~28px and scroll horizontally inside ScrollAreaBody. Content
 * may grow past the trigger; it must not be clamped to it.
 */

import * as React from "react";
import { Menu as MenuPrimitive } from "@base-ui/react/menu";

import { Check, ChevronRight } from "./glyphs";
import { cn } from "./cn";
import { Icon } from "./icon";
import { POPUP_SURFACE_DENSE } from "./popup-surface";
import { ScrollAreaBody, SCROLL_HOST_CLASS } from "./scroll-area";
import {
  parseShortcutString,
  Shortcut,
  type ShortcutKey,
} from "./shortcut";

/*
 * Base UI's `Menu.GroupLabel` reads `MenuGroupContext` and throws outright when
 * it is not inside a `Menu.Group`, which is the shape most menus are written in
 * (a label followed by loose items). Rather than make every call site wrap its
 * own group, the label wraps itself when nothing above it did. This flag is how
 * it knows: `DropdownMenuGroup` and `DropdownMenuRadioGroup` turn it on, and a
 * label reading `false` renders its own single-child `Menu.Group`. The public
 * API does not change either way.
 */
const InDropdownMenuGroup = React.createContext(false);

function DropdownMenu({ ...props }: MenuPrimitive.Root.Props) {
  return <MenuPrimitive.Root data-slot="dropdown-menu" {...props} />;
}

function DropdownMenuPortal({ ...props }: MenuPrimitive.Portal.Props) {
  return <MenuPrimitive.Portal data-slot="dropdown-menu-portal" {...props} />;
}

function DropdownMenuTrigger({ ...props }: MenuPrimitive.Trigger.Props) {
  return <MenuPrimitive.Trigger data-slot="dropdown-menu-trigger" {...props} />;
}

function DropdownMenuContent({
  align = "start",
  alignOffset = 0,
  side = "bottom",
  sideOffset = 4,
  className,
  children,
  ...props
}: MenuPrimitive.Popup.Props &
  Pick<
    MenuPrimitive.Positioner.Props,
    "align" | "alignOffset" | "side" | "sideOffset"
  >) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Positioner
        className="isolate z-50 outline-none"
        align={align}
        alignOffset={alignOffset}
        side={side}
        sideOffset={sideOffset}
      >
        <MenuPrimitive.Popup
          data-slot="dropdown-menu-content"
          className={cn(
            "z-50 max-h-(--available-height) min-w-(--anchor-width)",
            POPUP_SURFACE_DENSE,
            SCROLL_HOST_CLASS,
            className
          )}
          {...props}
        >
          <ScrollAreaBody overflow="vertical">{children}</ScrollAreaBody>
        </MenuPrimitive.Popup>
      </MenuPrimitive.Positioner>
    </MenuPrimitive.Portal>
  );
}

function DropdownMenuGroup({ ...props }: MenuPrimitive.Group.Props) {
  return (
    <InDropdownMenuGroup value={true}>
      <MenuPrimitive.Group data-slot="dropdown-menu-group" {...props} />
    </InDropdownMenuGroup>
  );
}

function DropdownMenuLabel({
  className,
  inset,
  ...props
}: MenuPrimitive.GroupLabel.Props & {
  inset?: boolean;
}) {
  const inGroup = React.use(InDropdownMenuGroup);

  const label = (
    <MenuPrimitive.GroupLabel
      data-slot="dropdown-menu-label"
      data-inset={inset}
      className={cn(
        "px-1.5 py-1 text-meta font-medium text-ink-subtle data-inset:pl-7",
        className
      )}
      {...props}
    />
  );

  return inGroup ? (
    label
  ) : (
    <MenuPrimitive.Group data-slot="dropdown-menu-label-group">
      {label}
    </MenuPrimitive.Group>
  );
}

function DropdownMenuItem({
  className,
  inset,
  variant = "default",
  ...props
}: MenuPrimitive.Item.Props & {
  inset?: boolean;
  variant?: "default" | "destructive";
}) {
  return (
    <MenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-inset={inset}
      data-variant={variant}
      className={cn(
        "group/dropdown-menu-item relative flex cursor-default items-center gap-1.5 rounded-badge px-1.5 py-1 text-label outline-hidden select-none focus:bg-hover focus:text-ink not-data-[variant=destructive]:focus:**:text-ink data-inset:pl-7 data-[variant=destructive]:text-risk data-[variant=destructive]:focus:bg-risk-bg data-[variant=destructive]:focus:text-risk data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 data-[variant=destructive]:*:[svg]:text-risk",
        className
      )}
      {...props}
    />
  );
}

function DropdownMenuSub({ ...props }: MenuPrimitive.SubmenuRoot.Props) {
  return <MenuPrimitive.SubmenuRoot data-slot="dropdown-menu-sub" {...props} />;
}

function DropdownMenuSubTrigger({
  className,
  inset,
  children,
  ...props
}: MenuPrimitive.SubmenuTrigger.Props & {
  inset?: boolean;
}) {
  return (
    <MenuPrimitive.SubmenuTrigger
      data-slot="dropdown-menu-sub-trigger"
      data-inset={inset}
      className={cn(
        "flex cursor-default items-center gap-1.5 rounded-badge px-1.5 py-1 text-label outline-hidden select-none focus:bg-hover focus:text-ink not-data-[variant=destructive]:focus:**:text-ink data-inset:pl-7 data-popup-open:bg-hover data-popup-open:text-ink data-open:bg-hover data-open:text-ink [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
    >
      {children}
      <Icon icon={ChevronRight} className="ml-auto" />
    </MenuPrimitive.SubmenuTrigger>
  );
}

function DropdownMenuSubContent({
  align = "start",
  alignOffset = -3,
  side = "right",
  sideOffset = 0,
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuContent>) {
  return (
    <DropdownMenuContent
      data-slot="dropdown-menu-sub-content"
      className={cn(
        "w-auto min-w-24",
        className
      )}
      align={align}
      alignOffset={alignOffset}
      side={side}
      sideOffset={sideOffset}
      {...props}
    />
  );
}

function DropdownMenuCheckboxItem({
  className,
  children,
  checked,
  inset,
  ...props
}: MenuPrimitive.CheckboxItem.Props & {
  inset?: boolean;
}) {
  return (
    <MenuPrimitive.CheckboxItem
      data-slot="dropdown-menu-checkbox-item"
      data-inset={inset}
      className={cn(
        "relative flex cursor-default items-center gap-1.5 rounded-badge py-1 pr-8 pl-1.5 text-label outline-hidden select-none focus:bg-hover focus:text-ink focus:**:text-ink data-inset:pl-7 data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      checked={checked}
      {...props}
    >
      <span
        className="pointer-events-none absolute right-2 flex items-center justify-center"
        data-slot="dropdown-menu-checkbox-item-indicator"
      >
        <MenuPrimitive.CheckboxItemIndicator>
          <Icon icon={Check} />
        </MenuPrimitive.CheckboxItemIndicator>
      </span>
      {children}
    </MenuPrimitive.CheckboxItem>
  );
}

function DropdownMenuRadioGroup({ ...props }: MenuPrimitive.RadioGroup.Props) {
  return (
    <InDropdownMenuGroup value={true}>
      <MenuPrimitive.RadioGroup
        data-slot="dropdown-menu-radio-group"
        {...props}
      />
    </InDropdownMenuGroup>
  );
}

function DropdownMenuRadioItem({
  className,
  children,
  inset,
  ...props
}: MenuPrimitive.RadioItem.Props & {
  inset?: boolean;
}) {
  return (
    <MenuPrimitive.RadioItem
      data-slot="dropdown-menu-radio-item"
      data-inset={inset}
      className={cn(
        "relative flex cursor-default items-center gap-1.5 rounded-badge py-1 pr-8 pl-1.5 text-label outline-hidden select-none focus:bg-hover focus:text-ink focus:**:text-ink data-inset:pl-7 data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className
      )}
      {...props}
    >
      <span
        className="pointer-events-none absolute right-2 flex items-center justify-center"
        data-slot="dropdown-menu-radio-item-indicator"
      >
        <MenuPrimitive.RadioItemIndicator>
          <Icon icon={Check} />
        </MenuPrimitive.RadioItemIndicator>
      </span>
      {children}
    </MenuPrimitive.RadioItem>
  );
}

function DropdownMenuSeparator({
  className,
  ...props
}: MenuPrimitive.Separator.Props) {
  return (
    <MenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn("-mx-1 my-1 h-px bg-line", className)}
      {...props}
    />
  );
}

function DropdownMenuShortcut({
  children,
  className,
  keys,
}: {
  keys?: readonly ShortcutKey[];
  children?: React.ReactNode;
  className?: string;
}) {
  const chord =
    keys ??
    (typeof children === "string" && children.length > 0
      ? parseShortcutString(children)
      : null);
  if (chord === null) return null;
  return <Shortcut keys={chord} className={cn("ml-auto", className)} />;
}

export {
  DropdownMenu,
  DropdownMenuPortal,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuItem,
  DropdownMenuCheckboxItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
};
