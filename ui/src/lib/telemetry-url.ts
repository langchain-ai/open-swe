function templateDashboardPath(pathname: string): string {
  if (/^\/agents\/reviews\/[^/]+\/[^/]+\/[^/]+\/?$/.test(pathname))
    return "/agents/reviews/:owner/:repo/:number"
  if (/^\/agents\/automations\/(?!new\/?$)[^/]+\/?$/.test(pathname))
    return "/agents/automations/:scheduleId"
  if (/^\/agents\/[^/]+\/plan\/?$/.test(pathname))
    return "/agents/:threadId/plan"
  if (
    /^\/agents\/[^/]+\/?$/.test(pathname) &&
    !/^\/agents\/(automations|instructions|reviews|sandbox|skills|threads|workspaces)\/?$/.test(
      pathname
    )
  )
    return "/agents/:threadId"
  if (/^\/review\/repositories\/[^/]+\/?$/.test(pathname))
    return "/review/repositories/:owner"
  if (/^\/[^/]+\/[^/]+\/pull\/[^/]+\/?$/.test(pathname))
    return "/:owner/:repo/pull/:number"
  return pathname
}

export function stripUrlDetails(url: string, dashboardOrigin?: string): string {
  const withoutDetails = url.split(/[?#]/, 1)[0] ?? ""
  // A prefix match, so the desktop app's `open-swe://app` counts whatever origin its custom scheme parses to.
  if (dashboardOrigin && withoutDetails.startsWith(`${dashboardOrigin}/`))
    return `${dashboardOrigin}${templateDashboardPath(withoutDetails.slice(dashboardOrigin.length))}`
  if (withoutDetails.startsWith("/"))
    return withoutDetails.startsWith("//")
      ? stripAbsoluteUrlDetails(withoutDetails, dashboardOrigin)
      : templateDashboardPath(withoutDetails)
  if (!/^https?:\/\//i.test(withoutDetails)) return withoutDetails
  return stripAbsoluteUrlDetails(withoutDetails, dashboardOrigin)
}

function stripAbsoluteUrlDetails(
  url: string,
  dashboardOrigin?: string
): string {
  try {
    const parsed = new URL(url, dashboardOrigin)
    const pathname =
      parsed.origin === dashboardOrigin
        ? templateDashboardPath(parsed.pathname)
        : parsed.pathname
    const prefix = url.startsWith("//") ? `//${parsed.host}` : parsed.origin
    return `${prefix}${pathname}`
  } catch {
    return url
  }
}
