/*
 * Label, on CORE 14.
 *
 * A plain semantic `<label>`, deliberately. Base UI's labelling lives on
 * `Field.Label`, which only exists inside a `Field.Root` and takes its `htmlFor`
 * from the field context; most of what this app labels is a control the caller
 * already owns an id for, so wrapping every one in a field to name it would be
 * ceremony. This is the element, on the type scale, with the one behaviour a
 * label owes: it goes quiet when the control it names is disabled.
 *
 * `text-label` is the 13px step, named for this job. `font-medium` is what
 * separates a label from the body text around it -- hierarchy from weight and
 * position first, which is why the three ink steps stay unspent here.
 *
 * LABEL_CLASS and HELP_CLASS are the two form rungs. Field labels, section
 * titles, dialog titles, and setting-row names share LABEL_CLASS. Help,
 * descriptions, and dialog copy share HELP_CLASS. Do not restate either
 * string at a call site.
 *
 * The disabled treatment reads two ways because a label sits on either side of
 * its control: `peer-disabled:` for the sibling form (`<input class="peer">`
 * followed by its label) and `group-data-disabled/*` is left to the caller,
 * since the patterns tier owns row-level disabling.
 */

import * as React from "react";

import { cn } from "./cn";

/** Field labels, section titles, and dialog titles. */
const LABEL_CLASS = "text-label font-medium text-ink";
/** Help, descriptions, and dialog copy under a title. */
const HELP_CLASS = "text-meta text-ink-subtle";

function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn(
        "inline-flex items-center gap-1.5 select-none peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
        LABEL_CLASS,
        className
      )}
      {...props}
    />
  );
}

export { HELP_CLASS, LABEL_CLASS, Label };
