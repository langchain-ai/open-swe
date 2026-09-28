/** Chromium net error names mapped to a short human label. */
const ERROR_MESSAGES: Readonly<Record<string, string>> = Object.freeze({
  ERR_NAME_NOT_RESOLVED: "DNS address could not be found",
  ERR_NAME_RESOLUTION_FAILED: "DNS address could not be found",
  ERR_CONNECTION_REFUSED: "Connection refused",
  ERR_CONNECTION_RESET: "Connection was reset",
  ERR_CONNECTION_CLOSED: "Connection was closed",
  ERR_CONNECTION_TIMED_OUT: "Connection timed out",
  ERR_INTERNET_DISCONNECTED: "No internet connection",
  ERR_TIMED_OUT: "Connection timed out",
  ERR_CERT_AUTHORITY_INVALID: "Certificate authority is not trusted",
  ERR_CERT_COMMON_NAME_INVALID: "Certificate hostname mismatch",
  ERR_CERT_DATE_INVALID: "Certificate is expired or not yet valid",
  ERR_TOO_MANY_REDIRECTS: "Too many redirects",
  ERR_ADDRESS_UNREACHABLE: "Address unreachable",
  ERR_BLOCKED_BY_CLIENT: "Blocked by the browser",
})

/**
 * Friendly text for a Chromium error. CDP reports errors as `net::ERR_*`,
 * Electron as bare `ERR_*`; both are accepted.
 */
export function describeBrowserError(description: string): string {
  const name = description.replace(/^net::/, "")
  const friendly = ERROR_MESSAGES[name]
  if (friendly) return friendly
  if (name.length > 0) return name
  return "Network error"
}
