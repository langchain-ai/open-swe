/**
 * URL helpers shared by both browser hosts. Only http(s) pages can load in the
 * in-app browser, so everything the user types is normalised to one of those
 * two schemes or rejected.
 */

const LOOPBACK_HOSTS: ReadonlySet<string> = new Set([
  "localhost",
  "127.0.0.1",
  "0.0.0.0",
  "::1",
  "[::1]",
])

const LOOPBACK_PREFIX_PATTERN =
  /^(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(?::|\/|$)/i

/**
 * An explicit scheme, as opposed to a bare `host:port`. A colon followed by a
 * digit is a port, so `example.com:8080` stays a host while `javascript:x`,
 * `mailto:x` and `data:x` are recognised as schemes and rejected below.
 */
const EXPLICIT_SCHEME_PATTERN = /^[a-z][a-z0-9+.-]*:(?!\d)/i

export function isLoopbackHost(host: string): boolean {
  return LOOPBACK_HOSTS.has(host.toLowerCase())
}

export type BrowserUrlErrorReason = "empty" | "parse" | "unsupported-protocol"

export class BrowserUrlError extends Error {
  override readonly name = "BrowserUrlError"

  constructor(
    readonly reason: BrowserUrlErrorReason,
    readonly protocol?: string
  ) {
    super(
      reason === "empty"
        ? "Enter a URL."
        : reason === "parse"
          ? "That doesn't look like a valid URL."
          : `Only http and https pages can open here${protocol ? ` (got ${protocol})` : ""}.`
    )
  }
}

/**
 * Normalise free-form input into a fully qualified `http(s)://` URL.
 *
 * - Bare loopback hosts (`localhost:5173`) become `http://…`.
 * - Bare public hosts (`example.com`) become `https://…`.
 * - Already-qualified URLs are validated and returned as `URL.href`.
 */
export function normalizeBrowserUrl(raw: string): string {
  const trimmed = raw.trim()
  if (trimmed.length === 0) throw new BrowserUrlError("empty")
  const useHttp = LOOPBACK_PREFIX_PATTERN.test(trimmed)
  const hasScheme = !useHttp && EXPLICIT_SCHEME_PATTERN.test(trimmed)
  const candidate = hasScheme
    ? trimmed
    : `${useHttp ? "http" : "https"}://${trimmed}`
  let parsed: URL
  try {
    parsed = new URL(candidate)
  } catch {
    throw new BrowserUrlError("parse")
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new BrowserUrlError("unsupported-protocol", parsed.protocol)
  }
  return parsed.href
}

/** Whether a URL is one the in-app browser can load at all. */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url)
    return protocol === "http:" || protocol === "https:"
  } catch {
    return false
  }
}

/** Short label for a tab strip: the host, or the raw string when unparseable. */
export function browserUrlHost(url: string): string {
  try {
    return new URL(url).host || url
  } catch {
    return url
  }
}

export function sameOrigin(left: string, right: string): boolean {
  try {
    return new URL(left).origin === new URL(right).origin
  } catch {
    return false
  }
}
