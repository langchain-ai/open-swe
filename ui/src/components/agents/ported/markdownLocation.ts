export interface MarkdownLocation {
  file: string;
  startLine: number;
  endLine: number;
}

/**
 * Parse a `#loc=<path>:<start>[-<end>]` sentinel href emitted by the reviewer
 * diff-grouping pass into a file + line range. Tolerant of an origin prefix the
 * markdown URL transform may prepend, and of percent-encoding. Returns null for
 * any href that isn't a location link.
 */
export function parseLocationHref(href?: string): MarkdownLocation | null {
  if (!href) return null;
  const marker = href.indexOf("#loc=");
  if (marker === -1) return null;
  let raw = href.slice(marker + "#loc=".length);
  try {
    raw = decodeURIComponent(raw);
  } catch {
    // Keep raw as-is if it isn't valid percent-encoding.
  }
  const colon = raw.lastIndexOf(":");
  if (colon <= 0) return null;
  const file = raw.slice(0, colon);
  const [startStr, endStr] = raw.slice(colon + 1).split("-");
  const start = Number.parseInt(startStr ?? "", 10);
  if (!Number.isFinite(start) || start <= 0) return null;
  const end = endStr !== undefined ? Number.parseInt(endStr, 10) : start;
  return {
    file,
    startLine: start,
    endLine: Number.isFinite(end) && end >= start ? end : start,
  };
}
