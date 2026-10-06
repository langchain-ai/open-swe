"use client";

/*
 * Search input, on CORE 14. The one filter field.
 *
 * SEARCH IS ONE FIELD, NOT A FORM. There is no submit, no button, no `<form>`,
 * and no `onSearch`. Typing is the search; the caller filters on every keystroke
 * off `onValueChange`. Everything below follows from that: a field with no
 * submit has room for exactly two things in its trailing lane, and both of them
 * are about the query rather than about running it.
 *
 * IT IS A COMPOSITION, NOT A NEW CONTROL. `Input` in its ordinary state, `Icon`,
 * `Kbd` and a ghost icon `Button` -- the same trailing-lane object `input-copy`
 * already settled on, at the same inset. A search field that measured
 * differently from every other field on the page would be a second answer to a
 * question `Input` answers, and the whole reason this file exists is that four
 * surfaces were each about to answer it themselves.
 *
 * PLACEMENT PICKS THE SIZE, AND SEARCH IS USUALLY INSIDE SOMETHING.
 * `compact` (28) is the default because the field's home is a rail, a popup
 * header or a card -- somewhere already inside a frame, where 28 is the rung.
 * `control` (32) is for a page toolbar, standing beside a Button and a Select.
 * This is the same two-step axis `Button` and `Select` carry, with the default
 * flipped, and it is a CVA variant rather than a call-site override for the
 * reason `Select` states: `cn` cannot resolve `h-control` against `h-control-sm`,
 * so an override is settled by stylesheet order and silently lands a 28px field
 * wearing a 10px corner.
 *
 * THE LEADING GLYPH IS THE LABEL. The field carries no visible label because the
 * magnifying glass already says what it is, in every product a reader has used.
 * That makes the accessible name load-bearing rather than optional, so `label`
 * is required and there is no way to render this field without one.
 *
 * THE TRAILING LANE HOLDS ONE THING AT A TIME. Empty field: the shortcut hint,
 * because that is the moment the hint is useful and the only moment there is
 * nothing else to say. Non-empty field: the clear control, because a query is
 * the only thing that can be undone. They never overlap and they never shift the
 * text, because the field reserves the lane at both sizes whichever one is in
 * it.
 *
 * THE CLEAR CONTROL IS OURS, NOT THE BROWSER'S. `type="search"` stays -- it is
 * the honest input type, it gets `role="searchbox"`, and it puts a Search key on
 * a phone keyboard -- but its native cancel button is suppressed and we draw the
 * affordance ourselves. Three reasons, any one of which is enough.
 * `::-webkit-search-cancel-button` is a non-standard WebKit pseudo-element:
 * Firefox draws nothing at all, so a third of readers would get no clear
 * affordance and the corner would mean different things per engine. It is
 * painted by the UA in a UA colour, so it ignores `[data-theme]` and cannot be
 * put on the density ladder or the icon ladder. And it is what forced the
 * gallery to write "once there is a query the browser's own clear control owns
 * that corner" -- an anatomy negotiated with the user agent instead of decided.
 * Ours is a `Button` with a real accessible name, and it returns focus to the
 * field, which the native one does not.
 *
 * Clearing is not a setting. There is no `clearable` prop: a filter you cannot
 * empty in one click is a bug in every surface, so it is not a choice to make
 * per call site.
 *
 * THE FIELD OWNS NO KEYS. No `onKeyDown`, no Escape-to-clear, no
 * `stopPropagation` anywhere. Surfaces bind `/` and Cmd+K on the window to focus
 * this field, and those handlers work by checking whether the event target is
 * already a field -- so anything this file did with a key would either swallow
 * the shortcut or fight the surface for it. Escape-to-clear is the tempting one
 * and it is the worst: inside a popover it would eat the dismissal. A caller who
 * wants a key binds it on the element it belongs to.
 *
 * `shortcut` takes one key, drawn as one `Kbd`. A chord is `Shortcut` — on
 * CommandInput, a menu item, or the command palette. This field's trailing
 * lane is reserved for one chip (`/` in the product), so a chord does not
 * belong here. The hint also becomes `aria-keyshortcuts`, so the affordance
 * is announced rather than only drawn.
 */

import * as React from "react";
import { cva } from "class-variance-authority";

import { Button } from "./button";
import { cn } from "./cn";
import { Search, X } from "./glyphs";
import { Icon } from "./icon";
import { Input } from "./input";
import { Kbd } from "./kbd";

type SearchInputSize = "compact" | "control";

/*
 * The field. Both lanes are reserved by padding at both sizes, so the text
 * measure never changes when the hint gives way to the clear control.
 *
 * The native cancel button is suppressed here rather than hidden globally: this
 * is the only `type="search"` in the system, and a rule in the stylesheet would
 * be law made somewhere nobody reading this file would find it.
 */
const searchInputVariants = cva(
  "[&::-webkit-search-cancel-button]:appearance-none [&::-webkit-search-decoration]:appearance-none",
  {
    variants: {
      size: {
        compact: "h-control-sm rounded-compact pr-8 pl-7",
        control: "h-control rounded-control pr-9 pl-8",
      },
    },
    defaultVariants: {
      size: "compact",
    },
  }
);

/*
 * Every lane is centred with `inset-y-0 my-auto` rather than a translate: each
 * of these has a definite height, so auto margins centre them without spending a
 * transform the press scale would then have to share.
 */
const GLYPH_CLASS: Record<SearchInputSize, string> = {
  compact: "left-2",
  control: "left-2.5",
};

/* The clear control, inset the way `input-copy` insets its copy button. */
const CLEAR_CLASS: Record<SearchInputSize, string> = {
  compact: "right-0",
  control: "right-0.5",
};

/* The hint sits further in than the control it replaces: it is a chip, not a target. */
const HINT_CLASS: Record<SearchInputSize, string> = {
  compact: "right-1.5",
  control: "right-2",
};

const LANE_CLASS = "pointer-events-none absolute inset-y-0 my-auto";

interface SearchInputProps
  extends Omit<
    React.ComponentProps<"input">,
    "children" | "onChange" | "size" | "type" | "value"
  > {
  /** Placement picks the size: `compact` inside a frame, `control` in a toolbar. */
  size?: SearchInputSize;
  /** The query. Controlled: this field holds no state of its own. */
  value: string;
  onValueChange: (value: string) => void;
  /**
   * The accessible name. Required: the glyph is the only visible label, so
   * without this the field announces as an unnamed text box.
   */
  label: string;
  /** One key to draw as a hint while the field is empty, e.g. "/". */
  shortcut?: string;
}

/** A single filter field: a glyph, a query, and one thing in the trailing lane. */
function SearchInput({
  className,
  label,
  onValueChange,
  placeholder = "Search",
  ref,
  shortcut,
  size = "compact",
  value,
  ...props
}: SearchInputProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);

  const setInput = React.useCallback(
    (node: HTMLInputElement | null) => {
      inputRef.current = node;
      if (typeof ref === "function") {
        ref(node);
      } else if (ref) {
        ref.current = node;
      }
    },
    [ref]
  );

  /* Focus goes back to the field: clearing is a step in searching, not an exit. */
  const clear = React.useCallback(() => {
    onValueChange("");
    inputRef.current?.focus();
  }, [onValueChange]);

  const hasQuery = value !== "";

  return (
    <div
      data-slot="search-input"
      data-size={size}
      className={cn("relative flex w-full items-center", className)}
    >
      <Icon
        icon={Search}
        size="sm"
        className={cn(LANE_CLASS, GLYPH_CLASS[size], "text-ink-subtle")}
      />
      <Input
        ref={setInput}
        type="search"
        data-slot="search-input-field"
        value={value}
        aria-label={label}
        aria-keyshortcuts={shortcut}
        placeholder={placeholder}
        className={cn(searchInputVariants({ size }))}
        onChange={(event) => onValueChange(event.target.value)}
        {...props}
      />
      {hasQuery ? (
        <Button
          variant="ghost"
          size="icon-sm"
          data-slot="search-input-clear"
          aria-label={`Clear ${label.toLowerCase()}`}
          className={cn("absolute inset-y-0 my-auto", CLEAR_CLASS[size])}
          onClick={clear}
        >
          <Icon icon={X} size="sm" />
        </Button>
      ) : null}
      {!hasQuery && shortcut !== undefined ? (
        <Kbd
          aria-hidden
          data-slot="search-input-shortcut"
          className={cn(LANE_CLASS, HINT_CLASS[size])}
        >
          {shortcut}
        </Kbd>
      ) : null}
    </div>
  );
}

export { SearchInput };
export type { SearchInputProps, SearchInputSize };
