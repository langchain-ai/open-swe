/*
 * Input, retokenized onto CORE 14.
 *
 * A control, so it takes control geometry: 32px on the density ladder and a
 * 10px radius, which is what sits next to a Button in a toolbar. The recessed
 * fill is `muted`, one step back from `panel` in both themes, so the field
 * reads as inset without the vendored dark-only tinted-fill branch. Focus and
 * invalid come from `field-focus` (border + thin soft ring); invalid borrows
 * the risk pair.
 *
 * The 16px base size is the iOS Safari zoom guard (Safari zooms the viewport on
 * focus for anything under 16px). From md up it matches the field label, so a
 * form stays on two rungs: text-label and text-meta. FIELD_VALUE_CLASS is that
 * one decision. Textarea, Combobox, and Select consume it; do not restate it.
 */

import * as React from "react";
import { Input as InputPrimitive } from "@base-ui/react/input";

import { cn } from "./cn";
import {
  FIELD_FOCUS_CLASS,
  FIELD_INVALID_CLASS,
} from "./field-focus";

/** 16px below md so iOS Safari does not zoom; label size from md up. */
const FIELD_VALUE_CLASS = "text-title md:text-label";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-control w-full min-w-0 rounded-control border border-line-strong bg-muted px-2.5 py-1 transition-colors duration-fast ease-out-quint outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-label file:font-medium file:text-ink placeholder:text-ink-subtle disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-hover disabled:opacity-50",
        FIELD_VALUE_CLASS,
        FIELD_FOCUS_CLASS,
        FIELD_INVALID_CLASS,
        className
      )}
      {...props}
    />
  );
}

export { FIELD_VALUE_CLASS, Input };
