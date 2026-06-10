import { clsx } from "clsx"
import { twMerge } from "tailwind-merge"
import type { ClassValue } from "clsx";

/**
 * Merge class names into a single string.
 * @param inputs - The class names to merge.
 * @returns The merged class name string.
 * @example
 * cn("text-red-500", "bg-blue-500") // "text-red-500 bg-blue-500"
 * cn("text-red-500", "bg-blue-500", "text-2xl") // "text-red-500 bg-blue-500 text-2xl"
 */
export function cn(...inputs: Array<ClassValue>) {
  return twMerge(clsx(inputs))
}

/**
 * Intl.RelativeTimeFormat instance for formatting relative times.
 */
const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

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
  const diffMs = ts - Date.now(); // negative for past
  const absMs = Math.abs(diffMs);

  if (absMs < 60_000) return rtf.format(Math.round(diffMs / 1000), "second");
  if (absMs < 60 * 60_000) return rtf.format(Math.round(diffMs / 60_000), "minute");
  if (absMs < 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (60 * 60_000)), "hour");
  }
  if (absMs < 7 * 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (24 * 60 * 60_000)), "day");
  }
  if (absMs < 30 * 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (7 * 24 * 60 * 60_000)), "week");
  }
  if (absMs < 365 * 24 * 60 * 60_000) {
    return rtf.format(Math.round(diffMs / (30 * 24 * 60 * 60_000)), "month");
  }
  return rtf.format(Math.round(diffMs / (365 * 24 * 60 * 60_000)), "year");
}