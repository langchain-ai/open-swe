import { createError, getRequestHeader, getRequestURL, proxyRequest } from "h3"

import { isSameOriginRequest } from "./local/guard"
import { localGraphConfiguration } from "./local/graph"

export { isSameOriginRequest, localGraphConfiguration }

export function localGraphTarget(requestUrl: URL, origin: string): string {
  const pathname =
    requestUrl.pathname.replace(/^\/local-graph(?=\/|$)/, "") || "/"
  return `${origin}${pathname}${requestUrl.search}`
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
