import { useRouter } from "@tanstack/react-router"

import { validateFrontendReviewSearch } from "@/features/reviews/frontend/search"

const STORAGE_KEY = "open-swe:last-app-location"
const SECTION_STORAGE_PREFIX = "open-swe:last-section-location:"
const FALLBACK_LOCATION = "/agents"

export const SECTION_ROOTS = [
  "/agents/skills",
  "/agents/automations",
  "/agents/bots",
  "/agents/reviews",
  "/agents/qa",
  "/incidents",
] as const

export type SectionRoot = (typeof SECTION_ROOTS)[number]

function isUnder(root: string, pathname: string | undefined): boolean {
  return pathname === root || Boolean(pathname?.startsWith(`${root}/`))
}

function pathnameOf(href: string): string | undefined {
  return href.split(/[?#]/, 1)[0]
}

function isAppLocation(value: string): boolean {
  const pathname = pathnameOf(value)
  return [FALLBACK_LOCATION, "/assistant", "/incidents"].some((root) =>
    isUnder(root, pathname)
  )
}

export function sectionOf(href: string): SectionRoot | undefined {
  const url = new URL(href, "http://localhost")
  const pathname = url.pathname
  if (
    isUnder("/agents/reviews", pathname) &&
    validateFrontendReviewSearch({
      view: url.searchParams.get("view"),
      run: url.searchParams.get("run"),
    }).view === "frontend"
  )
    return "/agents/qa"
  return SECTION_ROOTS.find((root) => isUnder(root, pathname))
}

export function rememberAppLocation(href: string): void {
  if (typeof window === "undefined" || !isAppLocation(href)) return
  window.sessionStorage.setItem(STORAGE_KEY, href)
  const section = sectionOf(href)
  if (section) {
    window.sessionStorage.setItem(`${SECTION_STORAGE_PREFIX}${section}`, href)
  }
}

export function getLastAppLocation(): string {
  if (typeof window === "undefined") return FALLBACK_LOCATION
  const href = window.sessionStorage.getItem(STORAGE_KEY)
  return href && isAppLocation(href) ? href : FALLBACK_LOCATION
}

export function getLastSectionLocation(section: SectionRoot): string {
  if (section === "/agents/qa") return section
  if (typeof window === "undefined") return section
  const href = window.sessionStorage.getItem(
    `${SECTION_STORAGE_PREFIX}${section}`
  )
  return href && sectionOf(href) === section ? href : section
}

/** Link `to` is a pathname only, so a saved href must be split into its parts. */
export function useHrefLinkOptions(): (href: string) => {
  to: string
  search: Record<string, unknown>
  hash: string | undefined
} {
  const router = useRouter()
  return (href) => {
    const url = new URL(href, "http://localhost")
    return {
      to: url.pathname,
      search: router.options.parseSearch(url.search),
      hash: url.hash.slice(1) || undefined,
    }
  }
}
