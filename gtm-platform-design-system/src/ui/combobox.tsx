"use client";

/*
 * Combobox, on CORE 14: the picker.
 *
 * COMPOSITION. Nothing new is installed. The trigger is an Input wearing a
 * popup, the surface is `ui/popover` with `inset="flush"` (POPUP_SURFACE_FLUSH
 * — same shell as every anchored float; Command owns the search + list inset),
 * and the list is `ui/command`, which is where filtering, scoring and roving
 * focus already live. ReUI's free autocomplete is the API reference and the
 * slot names follow it (`ComboboxInput` / `ComboboxContent` / `ComboboxEmpty` /
 * `ComboboxList` / `ComboboxItem`), so a builder who has read the ReUI docs can
 * compose this without a second dialect; what differs is that every visible
 * pixel comes from our primitives rather than from a second copy of a popup
 * and a list.
 *
 * WHY NOT SELECT. A Select is a closed list you read; a Combobox is an open one
 * you search. The line is the search field: the moment a picker needs one, the
 * options outran what a person can scan, and that is a different control rather
 * than a Select with a filter bolted on. Both are 32px on the density ladder
 * and both open a panel, so they read as siblings, which is the point.
 *
 * TRIGGER SHAPE. The trigger takes Input's geometry, fill, and FIELD_VALUE_CLASS
 * verbatim. A button never zooms the viewport, so the 16px guard buys this
 * control nothing on its own; it is here so a Combobox standing next to an
 * Input in the same form measures the same on a phone. `FIELD_TRIGGER_CLASS`
 * is exported for exactly one other consumer, `ui/date-picker`, whose trigger
 * is the same object: an input-shaped button that opens a panel.
 *
 * SINGLE SELECT ONLY. `value` is a string and selecting closes the popup. Multi
 * select is deliberately absent: it is not a prop away, it changes what the
 * trigger displays (chips, an overflow count, a clear affordance), whether the
 * popup closes on pick, and what the checkmark column means. That is a product
 * decision for the surface that first needs it, not a guess to make here.
 * TODO(multi-select): grow this file when a ticket asks for it, never fork it.
 */

import * as React from "react";

import { Check, ChevronsUpDown } from "./glyphs";
import { cn } from "./cn";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "./command";
import { FIELD_FOCUS_CLASS } from "./field-focus";
import { Icon } from "./icon";
import { FIELD_VALUE_CLASS } from "./input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "./popover";
/*
 * Input's geometry, fill and focus treatment, on a button. Shared with
 * `ui/date-picker`; see the header note before editing either copy of it.
 * Focus comes from FIELD_FOCUS_CLASS so the ring stays one recipe with Input.
 */
const FIELD_TRIGGER_CLASS = cn(
  "flex h-control w-full items-center justify-between gap-1.5 rounded-control border border-line-strong bg-muted px-2.5 py-1 whitespace-nowrap text-ink transition-[background-color,border-color] duration-fast ease-out-quint outline-none select-none hover:bg-hover disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-hover disabled:opacity-50 data-popup-open:bg-hover motion-reduce:transition-none",
  FIELD_VALUE_CLASS,
  FIELD_FOCUS_CLASS
);

/* The popup matches the trigger's width, because the list is the trigger opened. */
const CONTENT_CLASS = "w-(--anchor-width) min-w-48 overflow-y-hidden";

/* One list height for every picker in the product: 288px, about nine rows. */
const LIST_VIEWPORT_CLASS = "max-h-72";

/* The list itself no longer scrolls; the ScrollArea around it does. */
const LIST_CLASS = "max-h-none overflow-y-visible";

/* Room on the right for the selected-row checkmark. */
const ITEM_CLASS = "pr-8";

interface ComboboxContextValue {
  value?: string;
  onValueChange?: (value: string) => void;
  setOpen: (open: boolean) => void;
}

const ComboboxContext = React.createContext<ComboboxContextValue | null>(null);

function useComboboxContext(): ComboboxContextValue {
  const context = React.useContext(ComboboxContext);
  if (context === null) {
    throw new Error("Combobox parts must be rendered inside a <Combobox>.");
  }
  return context;
}

interface ComboboxProps {
  /** The selected option's value. Omit for an empty picker. */
  value?: string;
  onValueChange?: (value: string) => void;
  /** Controlled popup state. Omit and the picker owns it. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: React.ReactNode;
}

function Combobox({
  value,
  onValueChange,
  open,
  onOpenChange,
  children,
}: ComboboxProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(false);
  const isControlled = open !== undefined;

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) {
        setUncontrolledOpen(next);
      }
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange]
  );

  const context = React.useMemo<ComboboxContextValue>(
    () => ({ value, onValueChange, setOpen }),
    [value, onValueChange, setOpen]
  );

  return (
    <ComboboxContext.Provider value={context}>
      <Popover open={open ?? uncontrolledOpen} onOpenChange={setOpen}>
        {children}
      </Popover>
    </ComboboxContext.Provider>
  );
}

interface ComboboxTriggerProps
  extends React.ComponentProps<typeof PopoverTrigger> {
  /** Shown, quietly, while nothing is selected. */
  placeholder?: string;
}

function ComboboxTrigger({
  className,
  children,
  placeholder = "Select",
  ...props
}: ComboboxTriggerProps) {
  return (
    <PopoverTrigger
      data-slot="combobox-trigger"
      className={cn(FIELD_TRIGGER_CLASS, className)}
      {...props}
    >
      <span
        data-slot="combobox-value"
        className={cn("truncate text-left", children ? null : "text-ink-subtle")}
      >
        {children ?? placeholder}
      </span>
      <Icon icon={ChevronsUpDown} size="sm" className="text-ink-subtle" />
    </PopoverTrigger>
  );
}

function ComboboxContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof PopoverContent>) {
  return (
    /*
     * `inset` is fixed after the spread rather than destructured away: a
     * combobox body is a list that runs edge to edge, so a caller cannot pad
     * it, and settling that by ordering keeps the prop out of the signature
     * without leaving an unused binding to explain.
     */
    <PopoverContent
      data-slot="combobox-content"
      align="start"
      className={cn(CONTENT_CLASS, className)}
      {...props}
      inset="flush"
    >
      {/* Body of a flush popup — do not paint a second rounded panel. */}
      <Command className="rounded-none">{children}</Command>
    </PopoverContent>
  );
}

function ComboboxInput({
  ...props
}: React.ComponentProps<typeof CommandInput>) {
  return <CommandInput data-slot="combobox-input" {...props} />;
}

function ComboboxList({
  className,
  viewportClassName,
  children,
  ...props
}: React.ComponentProps<typeof CommandList>) {
  /*
   * CommandList already owns the vertical ScrollArea. A second wrapper here
   * nested two viewports and was a common source of phantom horizontal scroll.
   */
  return (
    <CommandList
      data-slot="combobox-list"
      className={cn(LIST_CLASS, className)}
      viewportClassName={cn(LIST_VIEWPORT_CLASS, viewportClassName)}
      {...props}
    >
      {children}
    </CommandList>
  );
}

function ComboboxEmpty({
  ...props
}: React.ComponentProps<typeof CommandEmpty>) {
  return <CommandEmpty data-slot="combobox-empty" {...props} />;
}

function ComboboxGroup({
  ...props
}: React.ComponentProps<typeof CommandGroup>) {
  return <CommandGroup data-slot="combobox-group" {...props} />;
}

function ComboboxSeparator({
  ...props
}: React.ComponentProps<typeof CommandSeparator>) {
  return <CommandSeparator data-slot="combobox-separator" {...props} />;
}

interface ComboboxItemProps
  extends Omit<React.ComponentProps<typeof CommandItem>, "onSelect" | "value"> {
  /** The value published to `onValueChange`. Never the label. */
  value: string;
}

function ComboboxItem({
  className,
  children,
  value,
  ...props
}: ComboboxItemProps) {
  const { value: selected, onValueChange, setOpen } = useComboboxContext();
  const isSelected = selected === value;

  /*
   * The handler closes over `value` rather than reading cmdk's callback
   * argument: cmdk lowercases the value it stores for scoring and hands that
   * back, so an option keyed `ACME_CORP` would be published as `acme_corp`.
   */
  function handleSelect(): void {
    onValueChange?.(value);
    setOpen(false);
  }

  return (
    <CommandItem
      data-slot="combobox-item"
      data-selected-value={isSelected ? "true" : undefined}
      value={value}
      onSelect={handleSelect}
      className={cn(ITEM_CLASS, className)}
      {...props}
    >
      {children}
      {isSelected ? (
        <span
          data-slot="combobox-item-indicator"
          className="pointer-events-none absolute right-2 flex items-center justify-center"
        >
          <Icon icon={Check} />
        </span>
      ) : null}
    </CommandItem>
  );
}

export {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxSeparator,
  ComboboxTrigger,
  FIELD_TRIGGER_CLASS,
};
export type { ComboboxItemProps, ComboboxProps, ComboboxTriggerProps };
