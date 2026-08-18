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

export default async function backendProxy(event: {
  req: Request
}): Promise<Response> {
  const url = new URL(event.req.url)

  // `redirect: "manual"` keeps the OAuth 3xx hops intact — following them here
  // would leave the browser's address bar where it started. The body is passed
  // through as a stream, which is what keeps webhook signatures verifiable.
  return fetch(`${backendOrigin()}${url.pathname}${url.search}`, {
    method: event.req.method,
    headers: event.req.headers,
    body: event.req.body,
    redirect: "manual",
    // @ts-expect-error -- required by undici to stream a request body
    duplex: "half",
  })
}
