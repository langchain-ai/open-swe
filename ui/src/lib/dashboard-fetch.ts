import { createIsomorphicFn } from "@tanstack/react-start"
import { getRequestHeader } from "@tanstack/react-start/server"

import { dashboardApiBase } from "./api-base"

/**
 * Origin for dashboard API requests. The browser uses a relative base so calls
 * stay same-origin; a server render has no relative base to resolve against and
 * goes straight to the backend.
 */
export const dashboardRequestOrigin = createIsomorphicFn()
  .client(() => dashboardApiBase())
  .server(() => (process.env.DASHBOARD_API_URL ?? "").replace(/\/$/, ""))

/**
 * `credentials: "include"` means nothing on the server, so the session cookie
 * has to be copied off the incoming request by hand.
 */
export const dashboardForwardedHeaders = createIsomorphicFn()
  .client((): Record<string, string> => ({}))
  .server((): Record<string, string> => {
    const cookie = getRequestHeader("cookie")
    return cookie ? { cookie } : {}
  })

export function dashboardApiUrl(path: string): string {
  return `${dashboardRequestOrigin()}/dashboard/api${path}`
}
