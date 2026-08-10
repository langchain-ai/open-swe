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
