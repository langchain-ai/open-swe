import { createError, getRequestHeader, getRequestURL, proxyRequest } from "h3"

interface LocalGraphConfiguration {
  origin: string
  token: string
}

export function localGraphConfiguration(
  environment: NodeJS.ProcessEnv = process.env
): LocalGraphConfiguration {
  const origin = new URL(environment.OPEN_SWE_LOCAL_GRAPH_ORIGIN || "")
  if (
    origin.protocol !== "http:" ||
    !["127.0.0.1", "::1"].includes(origin.hostname) ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  ) {
    throw new Error(
      "OPEN_SWE_LOCAL_GRAPH_ORIGIN must be a loopback HTTP origin"
    )
  }
  const token = environment.OPEN_SWE_LOCAL_GRAPH_TOKEN
  if (!token) throw new Error("OPEN_SWE_LOCAL_GRAPH_TOKEN is not configured")
  return { origin: origin.origin, token }
}

export function localGraphTarget(requestUrl: URL, origin: string): string {
  const pathname =
    requestUrl.pathname.replace(/^\/local-graph(?=\/|$)/, "") || "/"
  return `${origin}${pathname}${requestUrl.search}`
}

/**
 * Whether a request may carry the graph's bearer token.
 *
 * This route runs on loopback inside the user's browser profile, so any page
 * they visit can reach it. Without this gate a cross-site request would be
 * proxied to the graph fully authenticated, which starts agent runs — code
 * execution on the user's machine.
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

export default async function localGraphProxy(
  event: Parameters<typeof proxyRequest>[0]
) {
  const { origin, token } = localGraphConfiguration()
  if (
    !isSameOriginRequest(
      getRequestHeader(event, "origin"),
      getRequestHeader(event, "sec-fetch-site"),
      getRequestURL(event).origin
    )
  ) {
    throw createError({ statusCode: 403, statusMessage: "Forbidden" })
  }
  return proxyRequest(event, localGraphTarget(getRequestURL(event), origin), {
    headers: {
      authorization: `Bearer ${token}`,
      "accept-encoding": "identity",
    },
    fetchOptions: { redirect: "manual" },
  })
}
