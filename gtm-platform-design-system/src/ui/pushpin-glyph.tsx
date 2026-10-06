/* Upright thumbtack: flat head, flared body, and long needle stay legible at 16px. */
import type { SVGProps } from "react";

type PushPinProps = SVGProps<SVGSVGElement> & { title?: string; titleId?: string };

function PushPinMicro({ title, titleId, ...props }: PushPinProps) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden={title === undefined ? true : undefined} {...props}>
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <path d="M5 1h6v2h-1v4l2 2v1H8.75v3.5L8 15l-.75-1.5V10H4V9l2-2V3H5V1Z" />
    </svg>
  );
}

function PushPinMini({ title, titleId, ...props }: PushPinProps) {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden={title === undefined ? true : undefined} {...props}>
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <path d="M6 2h8v2h-1.5v5l2.5 2.5V13h-4v3.5L10 19l-1-2.5V13H5v-1.5L7.5 9V4H6V2Z" />
    </svg>
  );
}

function PushPinFull({ title, titleId, ...props }: PushPinProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden={title === undefined ? true : undefined} {...props}>
      {title !== undefined ? <title id={titleId}>{title}</title> : null}
      <path d="M7 2h10v2.5h-2v6l3 3V15h-5v5l-1 2-1-2v-5H6v-1.5l3-3v-6H7V2Z" />
    </svg>
  );
}

export { PushPinMicro, PushPinMini, PushPinFull };
