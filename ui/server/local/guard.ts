import { getRequestHeaders, getRequestUrl } from "@tanstack/react-start/server"

/**
 * Set only by the local server entry, which binds loopback. A hosted
 * deployment never sets it, so the local functions refuse to run there — they
 * read and write the machine the server runs on.
 */
export function localModeEnabled(
  environment: NodeJS.ProcessEnv = process.env
): boolean {
  return environment.OPEN_SWE_LOCAL_MODE === "1"
}

/**
 * Whether a request may carry the local server's authority.
 *
 * The server runs on loopback inside the user's browser profile, so any page
 * they visit can reach it. Without this gate a cross-site request would read
 * their files or start agent runs — code execution on their machine.
 *
 * A cross-origin `fetch` or form post always carries `Origin`; a same-origin
 * GET may omit it, so an absent `Origin` is only trusted when `Sec-Fetch-Site`
 * does not contradict it.
 */
export function isSameOriginRequest(
  requestOrigin: string | undefined,
  fetchSite: string | undefined,
  serverOrigin: string
): boolean {
  if (fetchSite && !["same-origin", "none"].includes(fetchSite)) return false
  if (requestOrigin === undefined) return true
  return requestOrigin === serverOrigin
}

export function assertLocalRequest(): void {
  if (!localModeEnabled()) {
    throw new Error("This server does not serve local endpoints")
  }
  const headers = getRequestHeaders()
  if (
    !isSameOriginRequest(
      headers.get("origin") ?? undefined,
      headers.get("sec-fetch-site") ?? undefined,
      getRequestUrl().origin
    )
  ) {
    throw new Error("Cross-origin requests may not reach local endpoints")
  }
}
