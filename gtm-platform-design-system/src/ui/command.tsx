"use client";

/*
 * Command, on CORE 14.
 *
 * The behaviour comes from cmdk rather than Base UI: filtering, scoring and
 * roving focus over a list are not a Base UI primitive, and cmdk is the
 * ecosystem default the shadcn anatomy is named after. Everything visible is
 * ours.
 *
 * The list is transparent on purpose. A command list is almost always the body
 * of a popover or a dialog, and those already draw the panel: giving the list a
 * second fill and a second radius would nest a card in a card, which is the
 * containment rule the design system exists to prevent. Items take the compact
 * radius, one step below whatever panel is hosting them.
 *
 * DENSITY. Searchable pickers (Combobox, filter menus) stay one step airier
 * than DropdownMenu / Select (`py-1.5` / `px-2`). The product command palette
 * is a jump list: `size="palette"` raises type to `text-body` and row height
 * to the data rung so destinations are easy to scan. Change THIS file when a
 * picker feels tight — every Command-backed surface inherits the picker size.
 *
 * THE LIST DOES NOT SCROLL; ITS SCROLLAREA DOES (wiki 02, the scrollbar law).
 * `CommandList` carried `max-h-72 overflow-y-auto`, so every command palette in
 * the product showed the platform's native bar with its track. The overflow
 * moved out to a ScrollArea wrapper -- the shape `ComboboxList` already proved
 * -- and the list itself is now `overflow-y-visible`. cmdk keeps the highlighted
 * row in view with `scrollIntoView`, which walks to the nearest scrollable
 * ancestor, so keyboard navigation is untouched by the move.
 *
 * WIDTH. A Command adapts to its host (`w-full min-w-0`). The list scrolls on
 * the vertical axis only — a horizontal thumb here always meant a flex row that
 * refused to shrink, not a second reading direction.
 *
 * The height bound went WITH the overflow, onto the viewport, because the
 * viewport is the box that scrolls: a `max-h-*` left on the list would only clip
 * inside a viewport that had already decided how tall it was. `viewportClassName`
 * is how a caller that wants a taller palette says so; `className` still dresses
 * the list.
 */

import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";

import { Search } from "./glyphs";
import { Inline } from "./box";
import { cn } from "./cn";
import { Icon } from "./icon";
import { ScrollArea } from "./scroll-area";
import {
  parseShortcutString,
  Shortcut,
  type ShortcutKey,
} from "./shortcut";

const CommandSizeContext = React.createContext<"picker" | "palette">("picker");

function Command({
  className,
  size = "picker",
  ...props
}: React.ComponentProps<typeof CommandPrimitive> & {
  size?: "picker" | "palette";
}) {
  return (
    <CommandSizeContext.Provider value={size}>
      <CommandPrimitive
        data-slot="command"
        data-size={size}
        className={cn(
          "group/command flex h-full w-full min-w-0 flex-col overflow-hidden rounded-panel text-ink",
          className
        )}
        {...props}
      />
    </CommandSizeContext.Provider>
  );
}

function CommandInput({
  className,
  shortcut,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Input> & {
  shortcut?: readonly ShortcutKey[];
}) {
  const size = React.useContext(CommandSizeContext);
  const palette = size === "palette";
  const typed = typeof props.value === "string" && props.value.length > 0;
  return (
    <div
      data-slot="command-input-wrapper"
      className={cn(
        "flex items-center border-b border-line",
        palette ? "h-row-record gap-2 px-3" : "h-control gap-1.5 px-2.5"
      )}
    >
      <Icon
        icon={Search}
        size={palette ? "md" : "sm"}
        className="text-ink-subtle"
      />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          "flex h-full w-full min-w-0 rounded-none border-0 bg-transparent py-1 text-ink outline-hidden placeholder:text-ink-subtle disabled:cursor-not-allowed disabled:opacity-50",
          palette ? "text-body" : "text-label",
          className
        )}
        {...props}
      />
      {shortcut === undefined || typed ? null : (
        <Shortcut keys={shortcut} className="text-ink-subtle" />
      )}
    </div>
  );
}

interface CommandListProps
  extends React.ComponentProps<typeof CommandPrimitive.List> {
  /** Classes for the scrolling box. The height bound lives here, not on the list. */
  viewportClassName?: string;
}

function CommandList({
  className,
  viewportClassName,
  children,
  ...props
}: CommandListProps) {
  const size = React.useContext(CommandSizeContext);
  return (
    <ScrollArea
      className="min-w-0 w-full"
      overflow="vertical"
      viewportClassName={cn(
        size === "palette" ? "max-h-96" : "max-h-72",
        viewportClassName
      )}
    >
      <CommandPrimitive.List
        data-slot="command-list"
        className={cn(
          "max-h-none min-w-0 scroll-py-1.5 overflow-y-visible",
          className
        )}
        {...props}
      >
        {children}
      </CommandPrimitive.List>
    </ScrollArea>
  );
}

function CommandEmpty({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className={cn(
        "py-6 text-center text-label text-ink-subtle group-data-[size=palette]/command:py-8 group-data-[size=palette]/command:text-body",
        className
      )}
      {...props}
    />
  );
}

function CommandGroup({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        "overflow-hidden p-1.5 text-ink [&+[cmdk-group]]:pt-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-meta [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:tracking-caps [&_[cmdk-group-heading]]:text-ink-subtle [&_[cmdk-group-heading]]:uppercase group-data-[size=palette]/command:p-2 group-data-[size=palette]/command:[&_[cmdk-group-heading]]:px-2.5 group-data-[size=palette]/command:[&_[cmdk-group-heading]]:py-2 group-data-[size=palette]/command:[&_[cmdk-group-heading]]:text-label",
        className
      )}
      {...props}
    />
  );
}

function CommandItem({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "group/command-item relative flex w-full min-w-0 cursor-default items-center gap-2 overflow-hidden rounded-compact px-2 py-1.5 text-label outline-hidden select-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-hover data-[selected=true]:text-ink [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 group-data-[size=palette]/command:min-h-row-data group-data-[size=palette]/command:gap-3 group-data-[size=palette]/command:px-2.5 group-data-[size=palette]/command:py-2 group-data-[size=palette]/command:text-body",
        className
      )}
      {...props}
    />
  );
}

function CommandSeparator({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Separator>) {
  return (
    <CommandPrimitive.Separator
      data-slot="command-separator"
      className={cn("-mx-1 h-px bg-line", className)}
      {...props}
    />
  );
}

function CommandShortcut({
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

function CommandFooter({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <Inline
      data-slot="command-footer"
      gap="md"
      align="center"
      wrap
      padding="sm"
      className={cn("border-t border-line text-meta text-ink-subtle", className)}
      {...props}
    />
  );
}

export {
  Command,
  CommandEmpty,
  CommandFooter,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
};
export type { CommandListProps };
