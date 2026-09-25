import { useEffect, useState } from "react"

// Matches the `max-md:` Tailwind breakpoint (md = 768px) used across the UI, so
// JS-driven layout decisions stay in sync with the CSS responsive utilities.
export const MOBILE_MEDIA_QUERY = "(max-width: 767px)"

function readMatches(query: string): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia(query).matches
}

/** Reactive flag that tracks whether `query` matches the viewport. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => readMatches(query))

  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setMatches(media.matches)
    onChange()
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [query])

  return matches
}

/** Reactive flag that tracks whether the viewport is at mobile width. */
export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_MEDIA_QUERY)
}
