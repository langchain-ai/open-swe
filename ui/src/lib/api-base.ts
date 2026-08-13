export function resolveDashboardApiBase(
  configured: string | undefined,
  protocol: string
): string {
  if (protocol === "open-swe:") return ""
  return (configured ?? "").replace(/\/$/, "")
}

export function dashboardApiBase(): string {
  const protocol = typeof window === "undefined" ? "" : window.location.protocol
  return resolveDashboardApiBase(
    import.meta.env.VITE_DASHBOARD_API_BASE_URL,
    protocol
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
