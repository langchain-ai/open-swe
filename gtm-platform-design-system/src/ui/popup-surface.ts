/*
 * Shared surface for every anchored float in the product.
 *
 * THE FAMILY. Popover, dropdown menu, select list, hover card, and any
 * composer-owned float that left the page are ONE containment step:
 *
 *   rounded-control · bg-panel · shadow-popup · ring-1 ring-line-strong
 *
 * Hand-rolling `border border-line` + `shadow-popup` is how we got a second
 * edge and mismatched padding — import these instead of restating the recipe.
 *
 * THREE INSETS, THREE JOBS:
 *
 *   dense  (p-1)  — item lists (DropdownMenu, Select). 4px so rounded-badge
 *                   items stay concentric with the 12px shell.
 *   padded (p-3)  — small documents (Popover, HoverCard, mention peeks).
 *   flush  (p-0)  — pickers whose body owns inset (Combobox/Command,
 *                   DatePicker/Calendar, composer mention popover). Shell +
 *                   motion only; never fight padded with a call-site `p-0`.
 *
 * Base UI popups enter from their positioner's origin with a short fade and
 * scale, then fade on exit. Reduced motion makes both state changes instant.
 *
 * Modal overlays (Dialog, Sheet) are a different family — see those files.
 */

import { cn } from "./cn";

/** Shell only: radius, fill, ONE edge, soft float. No padding, no motion. */
export const POPUP_SURFACE_SHELL =
  "rounded-control bg-panel text-ink shadow-popup ring-1 ring-line-strong outline-none";

/**
 * Shared enter/exit motion. Requires `--transform-origin` from the positioner.
 */
export const POPUP_SURFACE_MOTION =
  "origin-(--transform-origin) transition-[opacity,scale] duration-fast ease-out-quint data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none";

/** Shell + motion. Add a density class (or leave flush). */
export const POPUP_SURFACE = cn(POPUP_SURFACE_SHELL, POPUP_SURFACE_MOTION);

/** Item-list density: menus and select popups. */
export const POPUP_SURFACE_DENSE = cn(POPUP_SURFACE, "p-1");

/** Document density: popovers, hover cards, peeks. */
export const POPUP_SURFACE_PADDED = cn(POPUP_SURFACE, "p-3 text-body");

/**
 * Flush density: Combobox, DatePicker, and other pickers whose children set
 * their own inset (Command search + list, Calendar grid). Same shell/motion
 * as every other anchored float — just no padding on the popup itself.
 */
export const POPUP_SURFACE_FLUSH = POPUP_SURFACE;

/**
 * Rules restated for review. Prefer importing the class constants over
 * restating `shadow-popup` + radius + ring at a call site.
 */
export const POPUP_SURFACE_RULES = [
  "Anchored floats use POPUP_SURFACE_DENSE (menus/select), POPUP_SURFACE_PADDED (popover/hover card/peek), or POPUP_SURFACE_FLUSH when the content owns its own inset (Combobox, DatePicker, mention picker).",
  "Never pair shadow-popup with border border-line — the edge is ring-1 ring-line-strong.",
  "Never invent a fourth inset; dense is p-1, padded is p-3, flush is none.",
  "Combobox and DatePicker take PopoverContent inset=\"flush\" — do not hand-roll a second popup shell or fight padded with p-0.",
  "Composer menus use DropdownMenu / Popover — do not hand-roll a second popup shell.",
  "Dialogs and sheets are the overlay family, not this one.",
] as const;
