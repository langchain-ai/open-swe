import { proxyRequest } from "h3"

// Read per request, not at build time: which backend an instance fronts is a
// property of the deployment, so it lives in the pod's environment.
function backendOrigin(): string {
  const configured = (process.env.DASHBOARD_API_URL ?? "").replace(/\/$/, "")
  if (!configured) {
    throw new Error(
      "DASHBOARD_API_URL is not set. It is the backend this dashboard fronts; " +
        "there is no default because a fallback would be production's backend."
    )
  }
  return configured
}

export default async function backendProxy(
  event: Parameters<typeof proxyRequest>[0]
) {
  const url = new URL(event.req.url)

  // `redirect: "manual"` keeps the OAuth 3xx hops intact — following them here
  // would leave the browser's address bar where it started.
  return proxyRequest(event, `${backendOrigin()}${url.pathname}${url.search}`, {
    fetchOptions: { redirect: "manual" },
  })
}
