/*
 * Shared focus and invalid treatment for bordered form fields.
 *
 * THE FAMILY. Input, Textarea, Select trigger, and Combobox / DatePicker
 * triggers are one field: recessed or panel fill, `line-strong` hairline,
 * control radius. Focus must read the same on every one of them.
 *
 * MINIMAL RING. A 2px solid primary halo reads chunky on a 10px control and
 * fights the hairline. The recipe is border-color plus a 1px soft ring —
 * enough for keyboard visibility, quiet enough for CORE 14.
 *
 * Import these instead of restating `focus-visible:ring-*` at a call site.
 * Pressables without a field border (Button, Checkbox, Tabs) keep their own
 * focus treatment; they are not this family.
 */

/** Keyboard focus: primary border + thin soft ring. */
export const FIELD_FOCUS_CLASS =
  "focus-visible:border-primary focus-visible:ring-1 focus-visible:ring-primary/40";

/** Invalid answer: risk border + thin soft risk wash. */
export const FIELD_INVALID_CLASS =
  "aria-invalid:border-risk aria-invalid:ring-1 aria-invalid:ring-risk-bg";

/**
 * Always-on paint of FIELD_FOCUS_CLASS for gallery specimens that cannot
 * hold real focus. Same geometry; no `focus-visible:` prefix.
 */
export const FIELD_FOCUS_PAINTED_CLASS = "border-primary ring-1 ring-primary/40";
