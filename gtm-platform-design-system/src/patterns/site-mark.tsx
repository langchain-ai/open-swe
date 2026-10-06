"use client";

/*
 * Rules for SiteMark
 *
 * - A link to an external site wears that site's favicon at the data rung, and
 *   nothing else. No provider wordmark, no coloured chip, no letter avatar.
 * - The mark degrades in one direction only: favicon, then the globe glyph.
 *   A broken image never renders as a grey bullet, because a bullet reads as
 *   data rather than as a failed lookup.
 * - It is 14px and `shrink-0`. The row owns its own spacing; the mark never
 *   introduces a gap of its own.
 *
 * Extracted from the app's site switcher, which also carried a provider
 * registry. The registry is product data and stayed behind; the decision about
 * how an external site identifies itself in a row is the part that travels.
 */

import { useMemo, useState } from "react";

import { Globe } from "../ui/glyphs";
import { Icon } from "../ui/icon";

const MARK_CLASS = "size-3.5 shrink-0 rounded-tick object-contain";

function faviconSources(href: string): string[] {
  let host: string;
  try {
    host = new URL(href).hostname;
  } catch {
    return [];
  }
  if (host.length === 0) return [];
  return [
    `https://www.google.com/s2/favicons?sz=64&domain=${host}`,
    `https://icons.duckduckgo.com/ip3/${host}.ico`,
  ];
}

function SiteMark({ href }: { href: string }): React.ReactElement {
  const sources = useMemo(() => faviconSources(href), [href]);
  const [cursor, setCursor] = useState(0);
  const src = sources[cursor] ?? null;

  if (src === null) {
    return <Icon icon={Globe} size="sm" className={MARK_CLASS} />;
  }
  /*
   * A plain img with no referrer: the favicon endpoints 403 from localhost and
   * preview origins when a framework image proxy fetches them server-side, and
   * every one of those landed on the globe fallback.
   */
  return (
    <img
      src={src}
      alt=""
      width={14}
      height={14}
      referrerPolicy="no-referrer"
      aria-hidden
      data-slot="site-mark"
      onError={() => setCursor((at) => at + 1)}
      className={MARK_CLASS}
    />
  );
}

export { SiteMark };
