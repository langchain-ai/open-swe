/*
 * The one Icon wrapper (wiki 02: "one set, one wrapper").
 *
 * Heroicons solid, so there is no stroke weight here and no `strokeWidth` prop
 * anywhere in the product. What is left for this gate to own is the rung, and
 * the rung now decides more than a class: Heroicons draws three separate sets,
 * and a filled glyph drawn for 24px turns into a blob at 16. So `size` selects
 * the drawn set as well as the box.
 *
 *   sm  14px  micro (16/solid), scaled down
 *   md  16px  micro (16/solid)
 *   lg  20px  mini  (20/solid)
 *   nav 24px  full  (24/solid)
 *
 * The barrel hands over all three members per glyph (see `ui/glyphs`), which is
 * why call sites stay a single `icon={Mail}` and never name a set themselves.
 */

import type { Glyph } from "./glyphs";

import { cn } from "./cn";

type IconSize = "sm" | "md" | "lg" | "nav";

const ICON_SIZE_CLASS: Record<IconSize, string> = {
  sm: "size-3.5",
  md: "size-4",
  lg: "size-5",
  nav: "size-6",
};

/* Which of the three drawn sets each rung spends. */
const ICON_SET: Record<IconSize, keyof Glyph> = {
  sm: "micro",
  md: "micro",
  lg: "mini",
  nav: "full",
};

interface IconProps {
  /** A glyph from `./glyphs`, passed as a reference. */
  icon: Glyph;
  /** Ladder slot: 14 / 16 / 20 / 24. */
  size?: IconSize;
  /** Set only when the icon carries meaning on its own; otherwise it is decorative. */
  label?: string;
  /** Colour only (`text-ink-subtle`); geometry comes from `size`. */
  className?: string;
}

function Icon({ icon, size = "md", label, className }: IconProps) {
  const labelled = label !== undefined;
  const Drawn = icon[ICON_SET[size]];
  return (
    <Drawn
      data-slot="icon"
      className={cn("shrink-0", ICON_SIZE_CLASS[size], className)}
      aria-hidden={labelled ? undefined : true}
      aria-label={label}
      role={labelled ? "img" : undefined}
    />
  );
}

export { Icon };
export type { IconProps, IconSize };
