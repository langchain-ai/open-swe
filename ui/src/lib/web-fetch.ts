import { createIsomorphicFn } from "@tanstack/react-start"
import { getRequestHeader } from "@tanstack/react-start/server"

import { webApiBase } from "./api-base"

/**
 * Origin for web API requests. The browser uses a relative base so calls
 * stay same-origin; a server render has no relative base to resolve against and
 * goes straight to the backend.
 */
export const webRequestOrigin = createIsomorphicFn()
  .client(() => webApiBase())
  .server(() =>
    (process.env.WEB_API_URL ?? process.env.DASHBOARD_API_URL ?? "").replace(
      /\/$/,
      ""
    )
  )

/**
 * `credentials: "include"` means nothing on the server, so the session cookie
 * has to be copied off the incoming request by hand.
 */
export const webForwardedHeaders = createIsomorphicFn()
  .client((): Record<string, string> => ({}))
  .server((): Record<string, string> => {
    const cookie = getRequestHeader("cookie")
    return cookie ? { cookie } : {}
  })

export function webApiUrl(path: string): string {
  return `${webRequestOrigin()}/api${path}`
}

export const REQUEST_ID_HEADER = "X-Request-ID"

export function newRequestId(): string {
  return `req_${crypto.randomUUID()}`
}

/** A web API call that failed; `requestId` matches the server's logs. */
export class WebRequestError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly requestId?: string,
    options?: ErrorOptions
  ) {
    super(message, options)
  }
}

/** Wraps a fetch rejection so a request that never reached the server keeps its ID. */
export function networkError(cause: unknown, requestId: string): unknown {
  if (cause instanceof DOMException && cause.name === "AbortError") return cause
  return new WebRequestError(0, "Couldn't reach the server.", requestId, {
    cause,
  })
}
