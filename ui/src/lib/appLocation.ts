const STORAGE_KEY = "open-swe:last-app-location"
const FALLBACK_LOCATION = "/agents"

function isAppLocation(value: string): boolean {
  const pathname = value.split(/[?#]/, 1)[0]
  return [FALLBACK_LOCATION, "/assistant", "/incidents"].some(
    (root) => pathname === root || pathname?.startsWith(`${root}/`)
  )
}

export function rememberAppLocation(href: string): void {
  if (typeof window === "undefined" || !isAppLocation(href)) return
  window.sessionStorage.setItem(STORAGE_KEY, href)
}

export function getLastAppLocation(): string {
  if (typeof window === "undefined") return FALLBACK_LOCATION
  const href = window.sessionStorage.getItem(STORAGE_KEY)
  return href && isAppLocation(href) ? href : FALLBACK_LOCATION
}
