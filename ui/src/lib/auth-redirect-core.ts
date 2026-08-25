export const DEFAULT_AUTH_REDIRECT = "/agents"
export const AUTH_REDIRECT_STORAGE_KEY = "open-swe-auth-redirect"

type LocationParts = {
  pathname: string
  search?: string
  hash?: string
}

function browserOrigin(): string | null {
  return typeof window === "undefined" ? null : window.location.origin
}

function storage(): Storage | null {
  if (typeof window === "undefined") return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function isBlockedRedirectPath(path: string): boolean {
  return /^(?:\/login|\/dashboard\/api|\/_serverFn)(?:[/?#]|$)/.test(path)
}

export function sanitizeAuthRedirect(
  candidate: unknown,
  fallback = DEFAULT_AUTH_REDIRECT
): string {
  if (typeof candidate !== "string") return fallback
  const trimmed = candidate.trim()
  if (!trimmed) return fallback

  const origin = browserOrigin()
  const isProtocolRelative = trimmed.startsWith("//")
  const hasScheme = /^[a-zA-Z][a-zA-Z\d+.-]*:/.test(trimmed)
  if (isProtocolRelative) return fallback
  if (hasScheme && !origin) return fallback

  let parsed: URL
  try {
    parsed = new URL(trimmed, origin ?? "https://open-swe.invalid")
  } catch {
    return fallback
  }

  if ((hasScheme || origin) && origin && parsed.origin !== origin) {
    return fallback
  }

  const path = `${parsed.pathname}${parsed.search}${parsed.hash}`
  if (!path.startsWith("/") || isBlockedRedirectPath(path)) return fallback
  return path
}

export function authRedirectPathFromLocation(location: LocationParts): string {
  const hash = location.hash
    ? location.hash.startsWith("#")
      ? location.hash
      : `#${location.hash}`
    : ""
  return sanitizeAuthRedirect(
    `${location.pathname}${location.search ?? ""}${hash}`
  )
}

export function currentAuthRedirectPath(): string {
  if (typeof window === "undefined") return DEFAULT_AUTH_REDIRECT
  return authRedirectPathFromLocation(window.location)
}

export function rememberAuthRedirect(candidate: unknown): string {
  const path = sanitizeAuthRedirect(candidate)
  const s = storage()
  if (s) {
    try {
      s.setItem(AUTH_REDIRECT_STORAGE_KEY, path)
    } catch {}
  }
  return path
}

export function getRememberedAuthRedirect(): string | null {
  const s = storage()
  if (!s) return null
  let raw: string | null = null
  try {
    raw = s.getItem(AUTH_REDIRECT_STORAGE_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  const path = sanitizeAuthRedirect(raw, "")
  if (path) return path
  clearRememberedAuthRedirect()
  return null
}

export function clearRememberedAuthRedirect(): void {
  const s = storage()
  if (!s) return
  try {
    s.removeItem(AUTH_REDIRECT_STORAGE_KEY)
  } catch {}
}

export function consumeAuthRedirect(candidate?: unknown): string {
  const explicit = sanitizeAuthRedirect(candidate, "")
  const path = explicit || getRememberedAuthRedirect() || DEFAULT_AUTH_REDIRECT
  clearRememberedAuthRedirect()
  return path
}

export function authRedirectUrl(candidate?: unknown): string {
  const path = sanitizeAuthRedirect(candidate)
  const origin = browserOrigin()
  if (!origin) return path
  return new URL(path, origin).toString()
}
