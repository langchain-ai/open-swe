/*
 * The Spinner glyph: the one indeterminate loading mark.
 *
 * Heroicons solid has no arc spinner. ArrowPath is a refresh control, not a
 * loading state, so these three drawings are hand-tuned for the same micro /
 * mini / full viewBoxes the rest of the barrel uses. A faint track plus a
 * rounded arc is the modern indeterminate ring; `animate-spin` on `<Icon>`
 * does the rest.
 *
 * Enters the barrel as `Loader2`. `RefreshCw` keeps ArrowPath for reload.
 */

import type { SVGProps } from "react";

type SpinnerGlyphProps = SVGProps<SVGSVGElement> & {
  title?: string;
  titleId?: string;
};

function SpinnerMicro({ title, titleId, ...props }: SpinnerGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden={title === undefined ? true : undefined}
      {...props}
    >
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <circle
        cx="8"
        cy="8"
        r="6"
        stroke="currentColor"
        strokeWidth="1.5"
        opacity="0.2"
      />
      <circle
        cx="8"
        cy="8"
        r="6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="28.3 37.7"
        transform="rotate(-90 8 8)"
      />
    </svg>
  );
}

function SpinnerMini({ title, titleId, ...props }: SpinnerGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden={title === undefined ? true : undefined}
      {...props}
    >
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <circle
        cx="10"
        cy="10"
        r="7.5"
        stroke="currentColor"
        strokeWidth="1.75"
        opacity="0.2"
      />
      <circle
        cx="10"
        cy="10"
        r="7.5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeDasharray="35.3 47.1"
        transform="rotate(-90 10 10)"
      />
    </svg>
  );
}

function SpinnerFull({ title, titleId, ...props }: SpinnerGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden={title === undefined ? true : undefined}
      {...props}
    >
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="2"
        opacity="0.2"
      />
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray="42.4 56.5"
        transform="rotate(-90 12 12)"
      />
    </svg>
  );
}

export { SpinnerFull, SpinnerMicro, SpinnerMini };
