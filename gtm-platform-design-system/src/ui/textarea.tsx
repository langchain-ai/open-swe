/*
 * Textarea, on CORE 14.
 *
 * The same control as Input, given more than one line: identical `muted` fill,
 * `line-strong` hairline, 10px control radius, horizontal `px-2.5`, and the
 * same focus / `aria-invalid` recipes from `field-focus`. A field that means
 * the same thing has to look the same whether the answer is a name or a
 * paragraph, so the two files share those recipes and diverge only where a
 * multi-line box genuinely differs.
 *
 * Where it differs, and why:
 *
 * - `min-h-composer` (60px) instead of `h-control`. A textarea has no place on
 *   the density ladder's control rungs, which measure a single line; `composer`
 *   is the ladder step that already means "a multi-line text entry", and it
 *   opens the field at roughly three lines.
 * - `py-2`. A paragraph needs a little more vertical air than a single-line
 *   Input; radius, horizontal pad, and type stay shared.
 * - Disabled keeps opacity and cursor, but skips `bg-hover`: washing a full
 *   composer-height box with hover fill reads muddy. `text-ink-subtle` carries
 *   the quiet "locked" cue instead.
 * - `resize-y` and nothing else. Horizontal resize breaks the column a form is
 *   laid out in, and `resize-none` takes away the one affordance a user
 *   genuinely wants on a long answer. No custom grip chrome: the native handle
 *   is the whole of the resize UI.
 * - A native `<textarea>`, not Base UI's `Input` under a `render` prop. `Input`
 *   is typed to an `<input>`, so `rows` and the textarea value semantics would
 *   arrive through a cast; the element carries its own accessibility and the
 *   port is a class string, so there is nothing to gain by routing it.
 *
 * SCROLLING. A textarea overflows into the browser's own scrollbar and cannot
 * be wrapped in ScrollArea. That is not a hole in the scrollbar law: the law
 * governs app scroll surfaces, and this is the interior of a native control,
 * the same exemption a `<select>` popup has. Prefer `rows` (and copy that fits)
 * so the bar stays dormant; never paint a hand-styled `::-webkit-scrollbar`.
 *
 * Type is FIELD_VALUE_CLASS from Input: 16px Safari zoom guard, then the
 * label size from md up. Leading ships with that pair. Do not add leading-snug.
 */

import * as React from "react";

import { cn } from "./cn";
import {
  FIELD_FOCUS_CLASS,
  FIELD_INVALID_CLASS,
} from "./field-focus";
import { FIELD_VALUE_CLASS } from "./input";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "min-h-composer w-full min-w-0 resize-y rounded-control border border-line-strong bg-muted px-2.5 py-2 transition-colors duration-fast ease-out-quint outline-none placeholder:text-ink-subtle disabled:pointer-events-none disabled:cursor-not-allowed disabled:resize-none disabled:text-ink-subtle disabled:opacity-50",
        FIELD_VALUE_CLASS,
        FIELD_FOCUS_CLASS,
        FIELD_INVALID_CLASS,
        className
      )}
      {...props}
    />
  );
}

export { Textarea };
