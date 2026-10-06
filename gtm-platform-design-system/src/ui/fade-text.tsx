/*
 * FadeText — overflow that fades instead of ellipsizing.
 *
 * A painted gradient from the shell fill would lie on hover and selected
 * rows. The mask is the fade, so the row's own background shows through.
 * ScrollFadeContainer is the other job: more content in a scrolling pane.
 * This is the job for a clipped preview on a row.
 */

import type { ReactElement, ReactNode } from "react";

import { Box } from "./box";
import { cn } from "./cn";

const FADE_TEXT_RULES: readonly string[] = [
  "FadeText says there is more. It never uses an ellipsis. The cut is a trailing mask, not a painted wash, so hover and selected fills stay honest.",
  "Lines are 1, 2, or 3 of the meta rung (16px). Not a free max-height. Two is the preview default.",
  "This is not ScrollFadeContainer. A row preview does not scroll. A scrolling pane does not use this.",
];

const LINE_CLASS = {
  1: "max-h-4",
  2: "max-h-8",
  3: "max-h-12",
} as const;

const FADE_MASK_CLASS =
  "[mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)] [-webkit-mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)]";

type FadeTextLines = keyof typeof LINE_CLASS;

interface FadeTextProps {
  children: ReactNode;
  className?: string;
  /** Meta-rung lines. Two is the preview. */
  lines?: FadeTextLines;
  /** Defaults to a paragraph. A card button must pass a span. */
  render?: ReactElement;
}

function FadeText({
  children,
  className,
  lines = 2,
  render = <p />,
}: FadeTextProps) {
  return (
    <Box
      data-slot="fade-text"
      data-lines={lines}
      render={render}
      className={cn(
        "block w-full min-w-0 overflow-hidden",
        LINE_CLASS[lines],
        FADE_MASK_CLASS,
        className
      )}
    >
      {children}
    </Box>
  );
}

export { FadeText, FADE_TEXT_RULES };
export type { FadeTextLines, FadeTextProps };
