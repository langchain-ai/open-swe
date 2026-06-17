import { useEffect, useState } from "react"

// Matches the `max-md:` Tailwind breakpoint (md = 768px) used across the UI, so
// JS-driven layout decisions stay in sync with the CSS responsive utilities.
export const MOBILE_MEDIA_QUERY = "(max-width: 767px)"

function readIsMobile(): boolean {
  if (typeof window === "undefined") return false
  return window.matchMedia(MOBILE_MEDIA_QUERY).matches
}

/** Reactive flag that tracks whether the viewport is at mobile width. */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(readIsMobile)

  useEffect(() => {
    const media = window.matchMedia(MOBILE_MEDIA_QUERY)
    const onChange = () => setIsMobile(media.matches)
    onChange()
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [])

  return isMobile
}
