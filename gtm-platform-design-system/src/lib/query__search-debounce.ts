"use client";

/*
 * One wait for every `/v1` search `q`.
 *
 * A keystroke is not a request. The Query hook that owns the network call
 * runs the needle through this module; surfaces type freely. Closed or empty
 * flushes now so opening a browse page and clearing a box stay instant.
 * Typing waits `SEARCH_DEBOUNCE_MS` and one settled string is sent.
 *
 * Wiki 01 §16, wiki 02 Always, FDB-23, AGENTS.md. Do not add a second
 * SEARCH_DEBOUNCE_MS or a local setTimeout for search.
 */

import { useEffect, useState } from "react";

export const SEARCH_DEBOUNCE_MS = 300;

export function useDebouncedValue<T>(
  value: T,
  delay: number = SEARCH_DEBOUNCE_MS
): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [delay, value]);

  return debounced;
}

/**
 * Search needle for a Query hook. `null` / `undefined` / blank flush on this
 * turn. Any other string waits, so `N` → `No` → `Novar` is one request.
 */
function isOpenSearchNeedle(query: string | null | undefined): query is string {
  return query !== null && query !== undefined && query.trim() !== "";
}

export function useDebouncedSearchNeedle(
  query: string | null | undefined
): string | null | undefined {
  const [debounced, setDebounced] = useState(query);

  if (!isOpenSearchNeedle(query) && debounced !== query) {
    setDebounced(query);
  }

  useEffect(() => {
    if (!isOpenSearchNeedle(query)) return;
    const timer = window.setTimeout(() => setDebounced(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  return isOpenSearchNeedle(query) ? debounced : query;
}

export function settledSearchQuery(
  query: string | null | undefined
): string | undefined {
  const needle = query?.trim() ?? "";
  return needle.length > 0 ? needle : undefined;
}

/** The `q` a Query hook puts on the wire and in the key. */
export function useSettledSearchQuery(
  query: string | null | undefined
): string | undefined {
  return settledSearchQuery(useDebouncedSearchNeedle(query));
}
