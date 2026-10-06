/*
 * The static Orb glyph: the agent's mark when a button or label needs a
 * drawing rather than the animated loading canvas in `ui/orb.tsx`.
 *
 * Heroicons has no thought-orb. These three drawings are hand-tuned for the
 * same micro / mini / full viewBoxes the rest of the barrel uses, so `<Icon>`
 * can pick a set by rung without scaling a single path. The silhouette is a
 * dotted sphere on a tilted orbit — the still frame of the thinking-orbs
 * language the product already ships for agent activity.
 *
 * Name collision with the animated `Orb` component is intentional and follows
 * the same rule as `Box`: a file that needs both aliases the glyph
 * (`import { Orb as OrbGlyph }`).
 */

import type { SVGProps } from "react";

type OrbGlyphProps = SVGProps<SVGSVGElement> & {
  title?: string;
  titleId?: string;
};

function OrbMicro({ title, titleId, ...props }: OrbGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden={title === undefined ? true : undefined}
      {...props}
    >
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <circle cx="8" cy="8" r="1.4" />
      <circle cx="8" cy="2.4" r="1" />
      <circle cx="12.4" cy="4.2" r="1" />
      <circle cx="13.6" cy="8" r="1" />
      <circle cx="12.4" cy="11.8" r="1" />
      <circle cx="8" cy="13.6" r="1" />
      <circle cx="3.6" cy="11.8" r="1" />
      <circle cx="2.4" cy="8" r="1" />
      <circle cx="3.6" cy="4.2" r="1" />
      <circle cx="10.6" cy="6.2" r="0.85" />
      <circle cx="10.6" cy="9.8" r="0.85" />
      <circle cx="5.4" cy="9.8" r="0.85" />
      <circle cx="5.4" cy="6.2" r="0.85" />
    </svg>
  );
}

function OrbMini({ title, titleId, ...props }: OrbGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden={title === undefined ? true : undefined}
      {...props}
    >
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <circle cx="10" cy="10" r="1.55" />
      <circle cx="10" cy="2.8" r="1.1" />
      <circle cx="14.8" cy="4.6" r="1.1" />
      <circle cx="17.2" cy="10" r="1.1" />
      <circle cx="14.8" cy="15.4" r="1.1" />
      <circle cx="10" cy="17.2" r="1.1" />
      <circle cx="5.2" cy="15.4" r="1.1" />
      <circle cx="2.8" cy="10" r="1.1" />
      <circle cx="5.2" cy="4.6" r="1.1" />
      <circle cx="13.4" cy="7.4" r="0.95" />
      <circle cx="13.4" cy="12.6" r="0.95" />
      <circle cx="6.6" cy="12.6" r="0.95" />
      <circle cx="6.6" cy="7.4" r="0.95" />
      <circle cx="10" cy="6.2" r="0.85" />
      <circle cx="10" cy="13.8" r="0.85" />
    </svg>
  );
}

function OrbFull({ title, titleId, ...props }: OrbGlyphProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden={title === undefined ? true : undefined}
      {...props}
    >
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <circle cx="12" cy="12" r="1.75" />
      <circle cx="12" cy="3.2" r="1.2" />
      <circle cx="17.8" cy="5.4" r="1.2" />
      <circle cx="20.8" cy="12" r="1.2" />
      <circle cx="17.8" cy="18.6" r="1.2" />
      <circle cx="12" cy="20.8" r="1.2" />
      <circle cx="6.2" cy="18.6" r="1.2" />
      <circle cx="3.2" cy="12" r="1.2" />
      <circle cx="6.2" cy="5.4" r="1.2" />
      <circle cx="16.2" cy="8.6" r="1.05" />
      <circle cx="16.2" cy="15.4" r="1.05" />
      <circle cx="7.8" cy="15.4" r="1.05" />
      <circle cx="7.8" cy="8.6" r="1.05" />
      <circle cx="12" cy="7.2" r="0.95" />
      <circle cx="12" cy="16.8" r="0.95" />
      <circle cx="14.6" cy="12" r="0.9" />
      <circle cx="9.4" cy="12" r="0.9" />
    </svg>
  );
}

export { OrbFull, OrbMicro, OrbMini };
