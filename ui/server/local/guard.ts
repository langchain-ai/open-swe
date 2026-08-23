import {
  createError,
  getRequestHeader,
  getRequestURL,
  type proxyRequest,
} from "h3"

import { isSameOriginRequest } from "../local-graph-proxy"

type Event = Parameters<typeof proxyRequest>[0]

/**
 * Set only by the local server entry, which binds loopback. A hosted
 * deployment never sets it, so these routes do not exist there — they read and
 * write the machine the server runs on.
 */
export function localModeEnabled(
  environment: NodeJS.ProcessEnv = process.env
): boolean {
  return environment.OPEN_SWE_LOCAL_MODE === "1"
}

export function requireLocalMode(event: Event): void {
  if (!localModeEnabled()) {
    throw createError({ statusCode: 404, statusMessage: "Not found" })
  }
  if (
    !isSameOriginRequest(
      getRequestHeader(event, "origin"),
      getRequestHeader(event, "sec-fetch-site"),
      getRequestURL(event).origin
    )
  ) {
    throw createError({ statusCode: 403, statusMessage: "Forbidden" })
  }
}
