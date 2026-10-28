/**
 * Macaw's token-aware merge: stock tailwind-merge treats `text-secondary` and
 * `text-xs` as the same group and drops one.
 */
export { cn } from "@langchain/macaw-components/utils/cn"

/**
 * For Macaw `Link`s inside metadata text (authors, timestamps, GitHub links):
 * Link sets its own type size and colors its inner span as a link; these take
 * the surrounding size and color instead, so the anchor's own `text-*` classes show.
 */
export const INHERITED_LINK =
  "text-[length:inherit] leading-[inherit] tracking-[inherit] [&>span]:text-inherit [&>span]:hover:text-inherit"

/**
 * Format an elapsed duration in milliseconds as a compact string (e.g. "5s", "3m 20s").
 */
export function formatElapsed(ms: number): string {
  const secs = Math.max(1, Math.ceil(ms / 1000))
  if (secs < 60) return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m ${secs % 60}s`
}

/**
 * Intl.RelativeTimeFormat instance for formatting relative times.
 */
const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" })

/**
 * Format a timestamp as a relative time string.
 * @param ts - The timestamp to format.
 * @returns The relative time string.
 *
 * @example
 * formatRelativeTime(Date.now()) // "just now"
 * formatRelativeTime(Date.now() - 1000) // "1 second ago"
 * formatRelativeTime(Date.now() - 60 * 1000) // "1 minute ago"
 * formatRelativeTime(Date.now() - 60 * 60 * 1000) // "1 hour ago"
 * formatRelativeTime(Date.now() - 24 * 60 * 60 * 1000) // "1 day ago"
 * formatRelativeTime(Date.now() - 7 * 24 * 60 * 60 * 1000) // "1 week ago"
 */
export function formatRelativeTime(ts: number): string {
  const diffMs = ts - Date.now() // negative for past
  const absMs = Math.abs(diffMs)

  if (absMs < 60_000) return rtf.format(Math.round(diffMs / 1000), "second")
  if (absMs < 60 * 60_000)
    return rtf.format(Math.round(diffMs / 60_000), "minute")
  if (absMs < 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (60 * 60_000)), "hour")
  }
  if (absMs < 7 * 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (24 * 60 * 60_000)), "day")
  }
  if (absMs < 30 * 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (7 * 24 * 60 * 60_000)), "week")
  }
  if (absMs < 365 * 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (30 * 24 * 60 * 60_000)), "month")
  }
  return rtf.format(Math.round(diffMs / (365 * 24 * 60 * 60_000)), "year")
}
