import { createError, proxyRequest, setResponseStatus } from "h3"

// Read per request, not at build time: which backend an instance fronts is a
// property of the deployment, so it lives in the pod's environment.
export function dashboardBackendOrigin(
  env: NodeJS.ProcessEnv = process.env
): string | null {
  return (env.DASHBOARD_API_URL ?? "").replace(/\/$/, "") || null
}

export default async function backendProxy(
  event: Parameters<typeof proxyRequest>[0]
) {
  const url = new URL(event.req.url)
  const origin = dashboardBackendOrigin()
  if (!origin && url.pathname === "/dashboard/api/me") {
    setResponseStatus(event, 401)
    return { detail: "Not signed in" }
  }
  if (!origin) {
    throw createError({
      statusCode: 503,
      statusMessage: "Hosted backend is not configured",
    })
  }

  // `redirect: "manual"` keeps the OAuth 3xx hops intact — following them here
  // would leave the browser's address bar where it started.
  return proxyRequest(event, `${origin}${url.pathname}${url.search}`, {
    fetchOptions: { redirect: "manual" },
  })
}
