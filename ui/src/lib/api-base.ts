/**
 * Prefix for `/dashboard/api/*` requests: an explicit backend origin when the
 * frontend is served elsewhere, otherwise the path the app itself is served
 * under (Vite's `base`), which is "" for a root deployment and the mount prefix
 * when the backend serves the build under one.
 */
export function resolveDashboardApiBase(
  configured: string | undefined,
  protocol: string,
  basePath = "/"
): string {
  if (protocol === "open-swe:") return ""
  return (configured || basePath).replace(/\/$/, "")
}

export function dashboardApiBase(): string {
  const protocol = typeof window === "undefined" ? "" : window.location.protocol
  return resolveDashboardApiBase(
    import.meta.env.VITE_DASHBOARD_API_BASE_URL,
    protocol,
    import.meta.env.BASE_URL
  )
}

/**
 * True when the backend answers on a different origin than the dashboard, which
 * is what decides whether a server render can see the session: `osw_session` is
 * set on the API's origin, so a cross-origin deployment's own requests never
 * carry it however the browser's do.
 */
export function isCrossOriginApiBase(
  configured: string | undefined,
  requestOrigin: string
): boolean {
  if (!configured) return false
  try {
    return new URL(configured).origin !== requestOrigin
  } catch {
    return false
  }
}
