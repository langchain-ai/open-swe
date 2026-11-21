import { redirect } from "@tanstack/react-router"
import { createIsomorphicFn } from "@tanstack/react-start"
import { getCookie, getRequestUrl } from "@tanstack/react-start/server"

import { isCrossOriginApiBase } from "./api-base"
import { sanitizeAuthRedirect } from "./auth-redirect-core"
import { sessionQueryOptions } from "./session"
import type { QueryClient } from "@tanstack/react-query"

const PUBLIC_PATH_RE = /^\/(?:login|dashboard\/api|_serverFn)(?:[/?#]|$)/

/** The cookie the backend keeps a dashboard session in (`openswe/dashboard/oauth.py`). */
const SESSION_COOKIE = "osw_session"

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATH_RE.test(pathname)
}

/**
 * Settles what a server render can know of the session without asking the
 * backend, so neither the first byte nor the reads the head script starts with
 * it wait on `/me`. A visitor with no session cookie has no session: an app
 * route redirects them before the first byte, and the login page renders
 * signed out. One with a cookie gets the page while the session loads, and the
 * per-route `RequireLogin` gate sends them to sign in if `/me` turns it down;
 * the backend checks the cookie on every read either way.
 *
 * Client-side this is a no-op: the desktop app serves the prerendered shell from
 * `robin://app` with no server at all.
 */
export const resolveSessionOnServer = createIsomorphicFn()
  .client((_queryClient: QueryClient, _href: string) => {})
  .server((queryClient: QueryClient, href: string) => {
    // Prerendering the shell has no cookie, and redirecting would leave the
    // build without a shell to write.
    if (process.env.TSS_PRERENDERING === "true") return

    // A cross-origin deployment's `osw_session` belongs to the API's origin, so
    // this request cannot carry it and its absence says nothing. Auth
    // resolution stays client-side there.
    if (
      isCrossOriginApiBase(
        import.meta.env.VITE_DASHBOARD_API_BASE_URL,
        getRequestUrl().origin
      )
    ) {
      return
    }

    if (getCookie(SESSION_COOKIE)) return

    queryClient.setQueryData(sessionQueryOptions.queryKey, null)
    if (isPublicPath(href)) return
    throw redirect({
      to: "/login",
      search: { redirect: sanitizeAuthRedirect(href) },
    })
  })
